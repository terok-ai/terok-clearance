# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``org.terok.ClearanceVerdict1`` varlink interface contract."""

from __future__ import annotations

import asyncio

import pytest

from terok_clearance.verdict.interface import Verdict1Interface

from .conftest import CONTAINER, DOMAIN


@pytest.mark.asyncio
async def test_apply_forwards_helper_result() -> None:
    """Normal success path: helper's ``(ok, stderr)`` flows through unchanged."""

    async def helper(container: str, dest: str, action: str) -> tuple[bool, str]:
        assert (container, dest, action) == (CONTAINER, DOMAIN, "allow")
        return True, ""

    iface = Verdict1Interface(apply_verdict=helper)
    reply = await iface.Apply(container=CONTAINER, dest=DOMAIN, action="allow")
    assert reply.parameters == {"ok": True, "stderr": ""}


@pytest.mark.asyncio
async def test_apply_folds_unexpected_exception_into_structured_reply() -> None:
    """The "never raises" contract holds even when the helper explodes."""

    async def boom(container: str, dest: str, action: str) -> tuple[bool, str]:
        raise RuntimeError("helper exploded")

    iface = Verdict1Interface(apply_verdict=boom)
    reply = await iface.Apply(container=CONTAINER, dest=DOMAIN, action="allow")
    assert reply.parameters["ok"] is False
    assert "helper exploded" in reply.parameters["stderr"]


@pytest.mark.asyncio
async def test_apply_propagates_cancellation() -> None:
    """``CancelledError`` must escape so shutdown cooperates."""

    async def cancelled(container: str, dest: str, action: str) -> tuple[bool, str]:
        raise asyncio.CancelledError

    iface = Verdict1Interface(apply_verdict=cancelled)
    with pytest.raises(asyncio.CancelledError):
        await iface.Apply(container=CONTAINER, dest=DOMAIN, action="allow")
