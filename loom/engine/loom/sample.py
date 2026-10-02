"""A ready-made engagement from the files in samples/, for trying Loom end to end.

Edit the files in samples/ to try your own material: the kickoff call, the process document,
the replies, the workshop notes and the owner update are all plain text.
"""

from __future__ import annotations

from . import config, orchestrator

INTAKE = {"01-kickoff-call.vtt": "transcripts", "02-onboarding-process.md": "documents"}
LATER = {
    "responses": "03-replies-round1.txt",
    "workshop": "04-workshop-wall-notes.md",
    "tracking": "05-owner-update-jan.txt",
}


def create(start: bool = True) -> str:
    eid = orchestrator.create(
        "Sample: store onboarding",
        "New store staff take six weeks to work a till on their own; the COO wants under three weeks "
        "before three new stores open in spring.",
        sponsor="Eleni Georgiou",
        client="Demo Retail SA",
        sector="retail",
        challenge_type="Employee experience",
        people=[{"name": "Eleni Georgiou", "role": "Sponsor"}, {"name": "Maria", "role": "Store operations"},
                {"name": "Nikos", "role": "IT"}],
    )
    for name, kind in INTAKE.items():
        p = config.SAMPLES_DIR / name
        if p.exists():
            orchestrator.add_input(eid, kind, name, p.read_bytes())
    if start:
        from . import jobs

        jobs.enqueue("advance", eid)
    return eid


def later_file(kind: str):
    """The sample file to drop in at a later step (replies, workshop notes, owner update)."""
    name = LATER.get(kind)
    p = config.SAMPLES_DIR / name if name else None
    return p if p and p.exists() else None
