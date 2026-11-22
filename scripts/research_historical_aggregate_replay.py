"""Offline historical replay projecting only an absent, newly added optional field.

All archived derivative aggregate fields retain raw receipt verification. New temporal
continuation metadata is NOT attested, reconstructed into the tape, or used by the engine.
This does not modify the strict production/research verifier.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
from dataclasses import replace
from pathlib import Path

from algotrader.domain.models import DerivativesSnapshot
from scripts import research_evidence_contract as contract
from scripts.research_evidence_stream import read_bundle
from scripts.research_prospective import load
from scripts.research_recovery_replay import compare
from scripts.research_verified_replay import write_report


def legacy_aggregate_projection(value):
    if isinstance(value, (list, tuple)):
        return [legacy_aggregate_projection(item) for item in value]
    if isinstance(value, DerivativesSnapshot):
        return replace(value, continuation_inputs=None)
    return value


async def execute(capture: Path, definition: Path):
    bundle = read_bundle(capture)
    calls = (*bundle['record'].inputs, *bundle['supplemental'])
    if any(isinstance(c.result, DerivativesSnapshot) and c.result.continuation_inputs is not None
           for c in calls):
        raise ValueError('legacy projection forbidden for temporal-metadata captures')
    original = contract.normalized

    def historical_normalized(value):
        return original(legacy_aggregate_projection(value))

    # Isolated command, scoped across the original receipt verifier, restored even on error.
    contract.normalized = historical_normalized
    try:
        result = await compare(bundle, load(definition))
    finally:
        contract.normalized = original
    with capture.open('rb') as source:
        capture_sha256 = hashlib.file_digest(source, 'sha256').hexdigest()
    result['historical_compatibility'] = {
        'projection': 'only continuation_inputs absent in original capture; original aggregate fields verified',
        'continuation_metadata_attested': False,
        'continuation_publication_enabled': False,
        'capture_file_sha256': capture_sha256,
    }
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--capture', type=Path, required=True)
    p.add_argument('--definition', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        p.error('output exists')
    result = asyncio.run(execute(args.capture, args.definition))
    write_report(args.output, result)
    print({key: result[key]['status'] for key in ('core', 'recovery')})


if __name__ == '__main__':
    main()
