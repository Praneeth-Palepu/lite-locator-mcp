"""Run manual test cases with lite-locator's TestRunner and write a Playwright test script for each.

The LLM plans each case once; the library finds and acts on the elements itself and asks the LLM (a ~300-token
prompt) only when it is unsure. Failed cases are run again with the LLM choosing every element (the healer).

    pip install "lite-locator-mcp" playwright langchain-ollama      # or langchain-openai for Azure OpenAI
    playwright install chromium

    python run_test_cases.py --cases cases.txt --out specs/
    python run_test_cases.py --cases cases.txt --out specs/ --check-scripts      # also run them with npx playwright test

cases.txt: test cases separated by a line with ---, each with a Title line and numbered steps:
    Title: Search shows matching products
    1. Open https://practicesoftwaretesting.com
    2. Type pliers in the search box
    3. Click the search button
    4. Verify the text "Searched for: pliers" is shown
    ---
    Title: ...

LLM: Azure OpenAI when AZURE_OPENAI_ENDPOINT is set (also AZURE_OPENAI_API_KEY, AZURE_OPENAI_DEPLOYMENT,
OPENAI_API_VERSION), else a local Ollama model (OLLAMA_MODEL, default qwen3:4b-instruct).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from playwright.sync_api import sync_playwright

from lite_locator.runner import TestRunner


def make_llm():
    """(model for element choices, model for the one planning call per case)."""
    if os.environ.get("AZURE_OPENAI_ENDPOINT"):
        from langchain_openai import AzureChatOpenAI
        llm = AzureChatOpenAI(azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT"], temperature=0)
        return llm, llm.bind(response_format={"type": "json_object"})
    from langchain_ollama import ChatOllama
    model = os.environ.get("OLLAMA_MODEL", "qwen3:4b-instruct")
    return (ChatOllama(model=model, temperature=0, num_ctx=8192, num_predict=512),
            ChatOllama(model=model, temperature=0, num_ctx=8192, num_predict=2048, format="json"))  # JSON: cleaner plans


def read_cases(path):
    text = Path(path).read_text()
    if path.endswith(".json"):     # [{"id": .., "title": .., "steps": [..]}]
        return [(c.get("id") or f"TC{i:02d}", c["title"], "\n".join(f"{j}. {s}" for j, s in enumerate(c["steps"], 1)))
                for i, c in enumerate(json.loads(text), 1)]
    out = []
    for i, block in enumerate([b.strip() for b in re.split(r"^---\s*$", text, flags=re.M) if b.strip()], 1):
        title = next((x.split(":", 1)[1].strip() for x in block.splitlines() if x.lower().startswith("title:")),
                     f"test {i}")
        out.append((f"TC{i:02d}", title, block))
    return out


def run_case(llm, case, strict=False, headed=False):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=not headed)
        try:
            return TestRunner(llm[0], planner=llm[1], strict=strict).run(browser.new_page(), case)
        finally:
            browser.close()


def run_scripts(out, ids):
    """Run the written specs with Playwright Test; {id: passed?}. Needs Node and @playwright/test in `out`'s tree."""
    p = subprocess.run(["npx", "playwright", "test", *[f"{i}.spec.js" for i in ids], "--reporter=json", "--workers=1"],
                       cwd=out, capture_output=True, text=True)
    try:
        rep = json.loads(p.stdout)
    except ValueError:
        raise SystemExit("npx playwright test did not run: " + (p.stderr or p.stdout)[-500:]) from None
    res = {}

    def walk(s):
        for sp in s.get("specs", []):
            res[Path(sp["file"]).name.split(".")[0]] = sp["tests"][0]["results"][-1]["status"] == "passed"
        for ch in s.get("suites", []):
            walk(ch)
    for s in rep.get("suites", []):
        walk(s)
    return res


def add(total, usage, stage):
    for u in usage.values():
        t = total.setdefault(stage, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        for k in t:
            t[k] += u[k]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True, help="test cases: .txt (separated by ---) or .json")
    ap.add_argument("--out", default="specs", help="folder for the .spec.js files")
    ap.add_argument("--check-scripts", action="store_true", help="run the scripts with npx playwright test; heal "
                    "the ones that fail")
    ap.add_argument("--headed", action="store_true")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    llm, total, rows = make_llm(), {}, {}

    for cid, title, case in read_cases(a.cases):
        res = run_case(llm, case, headed=a.headed)
        add(total, res.usage, "run")
        mine = {}
        add(mine, res.usage, "run")
        healed = False
        if not res.passed:      # heal: run again with the LLM choosing every element
            res2 = run_case(llm, case, strict=True, headed=a.headed)
            add(total, res2.usage, "heal")
            add(mine, res2.usage, "heal")
            healed, res = True, (res2 if res2.passed or len(res2.log) > len(res.log) else res)
        (out / f"{cid}.spec.js").write_text(res.spec(f"{cid} {title}"))
        rows[cid] = dict(title=title, passed=res.passed, healed=healed, usage=mine,
                         chosen_by_library=sum(x["chosen_by"] == "library" for x in res.log),
                         chosen_by_llm=sum(x["chosen_by"] == "llm" for x in res.log),
                         failed=next((f"{x['step']}: {x['info'][:120]}" for x in res.log if not x["ok"]), ""))
        print(f"{cid} {'PASS' if res.passed else 'FAIL'}{' (healed)' if healed else ''}  {title}"
              + (f"\n     {rows[cid]['failed']}" if rows[cid]["failed"] else ""), flush=True)

    if a.check_scripts:
        if not shutil.which("npx"):
            raise SystemExit("--check-scripts needs Node.js (npx) and `npm i -D @playwright/test` in the output folder")
        ok = run_scripts(out, list(rows))
        cases = {cid: (title, case) for cid, title, case in read_cases(a.cases)}
        for cid, passed in ok.items():
            if not passed and rows[cid]["passed"]:     # the run passed but the script doesn't: heal it once
                title, case = cases[cid]
                res = run_case(llm, case, strict=True, headed=a.headed)
                add(total, res.usage, "heal")
                add(rows[cid]["usage"], res.usage, "heal")
                (out / f"{cid}.spec.js").write_text(res.spec(f"{cid} {title}"))
                passed, rows[cid]["healed"] = run_scripts(out, [cid]).get(cid, False), True
            rows[cid]["script"] = passed
            print(f"script {cid}: {'passed' if passed else 'FAILED'}")

    print("\nLLM usage")
    for stage, t in total.items():
        print(f"  {stage:5} {t['calls']:4} calls  {t['input_tokens']:8,} input  {t['output_tokens']:7,} output tokens")
    n = len(rows)
    print(f"  {sum(r['passed'] for r in rows.values())}/{n} test cases passed; scripts in {out}/")
    (out / "results.json").write_text(json.dumps(dict(cases=rows, usage=total), indent=1))


if __name__ == "__main__":
    main()
