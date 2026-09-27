"""Make the evergreen bank's episodes once, so a guest can hear them for free.

A listener with no account sees the evergreen bank on every myFAM rail and in
DailyFAM's example playlist, and a guest's tap on one of those plays **only**
an episode whose script is current and whose audio is already kept - it never
writes a script and never wakes the voice (`app._guest_play_gated`). So on a
deployment where nobody has made them, every sample tile asks for an account.

This is the one place those episodes are made: one script and one voicing per
bank topic, written into the shared cache exactly as a myFAM tap would write
them (same key, same length, same voice draw, origin `myfam` so none of them
is on Explore), and the audio kept beside each (§132). That is a one-off cost
paid by whoever runs this - roughly one episode per topic - and never by a
guest. A script is evergreen and slides forward on each play, so a bank that
is being listened to stays made; re-run it after a wipe or a long quiet spell.

    python tools/warm_guest_bank.py --dry-run   which are made, spends nothing
    python tools/warm_guest_bank.py             make the ones that are not
    python tools/warm_guest_bank.py --only fed-next-move ai-agents

Needs what an ordinary episode needs: an Anthropic key and a production voice.
With no production voice nothing can be kept, and it says so and stops.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report which bank episodes a guest can play; spend nothing")
    parser.add_argument("--only", nargs="*", default=None,
                        help="bank topic ids to make (default: the whole bank)")
    args = parser.parse_args(argv)

    import app
    import topics as topics_mod
    from pipeline import GenerationStats

    minutes = app.BROWSE_MINUTES
    topics = [t for t in topics_mod.TOPIC_BANK
              if args.only is None or t.id in args.only]
    if args.only:
        unknown = set(args.only) - {t.id for t in topics}
        if unknown:
            print(f"not in the bank: {', '.join(sorted(unknown))}")
            return 2

    made = missing = 0
    for topic in topics:
        plan = app._validated_plan(topic.query, minutes)
        voice = app._episode_voice("", "myfam", plan, "")
        pipeline = app._make_pipeline(voice, author="")
        pipeline.origin = "myfam"
        if not pipeline._keeps_audio():
            print("No production voice on this machine, so nothing can be kept "
                  "for a guest to replay. Run this where Chatterbox speaks "
                  "(python verify_voice.py says whether it does).")
            return 1
        if await pipeline.has_stored_audio(plan):
            made += 1
            print(f"  ready    {topic.id}")
            continue
        missing += 1
        if args.dry_run:
            print(f"  missing  {topic.id}")
            continue
        print(f"  making   {topic.id} ...", flush=True)
        stats = GenerationStats()
        async for _chunk in pipeline.stream_pcm(plan, stats):
            pass
        ok = await pipeline.has_stored_audio(plan)
        print(f"           {'kept' if ok else 'NOT kept - see the server log'}"
              f" ({stats.sentences} sentences)")
        made += int(ok)
    print(f"{made} of {len(topics)} bank episodes can be heard by a guest"
          + (f"; {missing} would be made" if args.dry_run and missing else "."))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
