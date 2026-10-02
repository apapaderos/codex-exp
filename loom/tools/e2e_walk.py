"""End-to-end browser walk: one sample engagement from New engagement to Close, clicking the
real interface. Works in demo mode (LOOM_RUNNER=stub, ~1 minute) and live mode (~30 minutes).

    pip install playwright && playwright install chromium
    LOOM_RUNNER=stub LOOM_UNDO_SECONDS=2 ./start.sh          # in one terminal
    python tools/e2e_walk.py --base http://localhost:8080 --shots /tmp/loom-shots

Exits non-zero if any step does not reach its expected state. Screenshots per step go to --shots.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from playwright.sync_api import Page, sync_playwright


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--shots", default="e2e-shots")
    ap.add_argument("--step-timeout", type=int, default=2400, help="seconds to wait for each step (live runs are slow)")
    ap.add_argument("--chromium", default=os.environ.get("CHROMIUM_PATH"), help="optional browser executable")
    a = ap.parse_args()
    os.makedirs(a.shots, exist_ok=True)
    t0, n = time.time(), [0]

    def shot(pg: Page, name: str) -> None:
        pg.evaluate("const w=document.getElementById('thread-wrap'); if (w) w.scrollTop = 1e9")
        n[0] += 1
        pg.screenshot(path=f"{a.shots}/{n[0]:02d}-{name}.png")
        print(f"[{int(time.time() - t0):>5}s] {name}", flush=True)

    def wait_turn(pg: Page, want: tuple[str, ...] = ("you", "wait", "done")) -> None:
        end = time.time() + a.step_timeout
        while time.time() < end:
            time.sleep(2)
            cls = pg.locator(".next-bar").first.get_attribute("class") or ""
            if any(f" {w}" in cls for w in want) and not pg.locator(".progress").count():
                time.sleep(1)
                return
        raise TimeoutError("step did not finish: " + pg.locator(".next-bar").first.inner_text())

    with sync_playwright() as p:
        browser = p.chromium.launch(**({"executable_path": a.chromium} if a.chromium else {}))
        pg = browser.new_page(viewport={"width": 1440, "height": 900})
        pg.on("dialog", lambda d: d.accept())
        pg.goto(a.base + "/new")
        pg.click("text=Try it with the sample onboarding files")
        wait_turn(pg); shot(pg, "understand")
        pg.fill("#composer-text", "How retailers get new store staff working a till alone within three weeks")
        pg.click("button[data-decision=approve]")
        wait_turn(pg); shot(pg, "research")
        pg.click("button[data-decision=send]")
        wait_turn(pg); shot(pg, "waiting-replies")
        pg.click("text=Add the sample replies"); time.sleep(1)
        pg.click("button[data-decision='continue:responses']")
        wait_turn(pg); shot(pg, "research-round-2")
        pg.click("button[data-decision=approve]:has-text('Enough research')")
        wait_turn(pg); shot(pg, "workshop-design")
        pg.click("button[data-decision=approve]")
        wait_turn(pg); shot(pg, "waiting-workshop")
        pg.click("text=Add the sample workshop notes"); time.sleep(1)
        pg.click("button[data-decision='continue:workshop']")
        wait_turn(pg); shot(pg, "hand-over")
        pg.click("button[data-decision=approve]")
        wait_turn(pg); shot(pg, "track")
        pg.click("text=Add the sample owner update"); time.sleep(1)
        pg.click("button[data-decision='continue:tracking']")
        wait_turn(pg); shot(pg, "track-updated")
        pg.click("button[data-decision=close]")
        wait_turn(pg, want=("done",)); shot(pg, "closed")
        browser.close()
    print(f"OK: full engagement in {int(time.time() - t0)}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
