"""Synchronisation utilities between the database and the WireGuard runtime.

When peers are added outside of the bot (e.g. via WireGuard Dashboard or
manual commands), the database may become out of sync with the runtime
state.  This module provides functions to reconcile the two, ensuring
that every runtime peer has a corresponding database entry and that
addresses in the database reflect the actual runtime configuration.
"""

import time
import app.context as ctx
from app.services import wg as WG


async def _ensure_system_user():
    """Ensure that a system user with ``tg_id=0`` exists for external peers."""
    # For peers created outside of the bot (in WGDashboard/manually), we
    # place them under a special user 'external' (tg_id=0) so that
    # admins can see who they belong to.  If the user does not exist
    # yet, it is created on the fly.
    u = await ctx.DBH.get_user(0)
    if u:
        return u
    # create the 'external' user with tg_id=0
    await ctx.DBH.get_or_create_user(0, "external", "External", "Peers")
    return await ctx.DBH.get_user(0)


async def resync_from_runtime() -> None:
    """Synchronise the database with the current state of WireGuard.

    This function performs two primary tasks:

    * Adds missing peers to the database if they exist in the runtime
      but not in the database.  These are assigned to the system user
      (tg_id=0) with placeholder keys.
    * Updates address fields on existing peer records if the runtime
      addresses differ from those stored in the database.
    """
    sys_user = await _ensure_system_user()
    now = int(time.time())
    rt_peers = WG.runtime_peers()  # list of dictionaries
    for p in rt_peers:
        row = await ctx.DBH.get_peer_by_pub(p["public_key"])
        if row:
            # Update addresses if they have changed
            if row["address_v4"] != p["address_v4"] or row["address_v6"] != p["address_v6"]:
                await ctx.DBH.execute(
                    "UPDATE peers SET address_v4=?, address_v6=?, updated_at=? WHERE id=?",
                    (p["address_v4"], p["address_v6"], now, row["id"])
                )
        else:
            # For an external peer we do not know the private key;
            # insert a placeholder and mark as belonging to the system user.
            name = f"external-{p['public_key'][-6:]}"
            await ctx.DBH.create_peer(
                user_id=sys_user["id"], name=name,
                public_key=p["public_key"], private_key="UNKNOWN",
                address_v4=p["address_v4"], address_v6=p["address_v6"],
                preshared_key=None
            )