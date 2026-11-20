"""Network-disabled strict replay of every rolling qualification decision in a study."""
from __future__ import annotations

import argparse
import asyncio
from datetime import timedelta
from pathlib import Path

from algotrader.application.rolling_discovery import verify_discovery_frame
from algotrader.infrastructure.rolling_discovery import read_discovery_frame, read_rolling_summaries
from scripts.research_prospective import collection_end, session
from scripts.research_verified_replay import offline, write_report


async def verify(root):
    with offline():
        definition, enrollment = session(root)
        if "rolling" not in definition.get("recovery", {}):
            raise ValueError("study has no rolling recovery policy")
        path = root / "recovery" / "episodes.sqlite"
        summaries = read_rolling_summaries(path)
        for summary in summaries:
            frame = read_discovery_frame(path, summary["decision_id"])
            if (frame["study_id"] != definition["study_id"]
                    or not definition["issued_at"] <= frame["issued_at"] <= collection_end(enrollment) + timedelta(seconds=120)):
                raise ValueError("rolling decision study/window mismatch")
            await verify_discovery_frame(frame, definition["recovery"]["rolling"]["configuration"])
        return {"study_id": definition["study_id"], "decisions_verified": len(summaries),
                "mode": "strict_rolling_paired_replay", "performance_claims_permitted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_report(args.output, asyncio.run(verify(args.study)))


if __name__ == "__main__":
    main()
