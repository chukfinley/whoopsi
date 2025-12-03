package com.whoopcapture

import java.util.UUID

object WhoopUuids {
    // Maverick / Whoop 5.0
    val MAVERICK_SERVICE: UUID = UUID.fromString("fd4b0001-cce1-4033-93ce-002d5875f58a")
    val MAVERICK_DATA: UUID    = UUID.fromString("fd4b0005-cce1-4033-93ce-002d5875f58a")
    val MAVERICK_EVENTS: UUID  = UUID.fromString("fd4b0004-cce1-4033-93ce-002d5875f58a")
    val MAVERICK_CMD_FROM: UUID = UUID.fromString("fd4b0003-cce1-4033-93ce-002d5875f58a")
    val MAVERICK_CMD_TO: UUID  = UUID.fromString("fd4b0002-cce1-4033-93ce-002d5875f58a")

    // Gen 4
    val GEN4_SERVICE: UUID  = UUID.fromString("61080001-8d6d-82b8-614a-1c8cb0f8dcc6")
    val GEN4_DATA: UUID     = UUID.fromString("61080005-8d6d-82b8-614a-1c8cb0f8dcc6")
    val GEN4_EVENTS: UUID   = UUID.fromString("61080004-8d6d-82b8-614a-1c8cb0f8dcc6")
    val GEN4_CMD_TO: UUID   = UUID.fromString("61080002-8d6d-82b8-614a-1c8cb0f8dcc6")
    val GEN4_CMD_FROM: UUID = UUID.fromString("61080003-8d6d-82b8-614a-1c8cb0f8dcc6")

    // Puffin
    val PUFFIN_SERVICE: UUID  = UUID.fromString("11500001-6215-11ee-8c99-0242ac120002")
    val PUFFIN_DATA: UUID     = UUID.fromString("11500005-6215-11ee-8c99-0242ac120002")
    val PUFFIN_EVENTS: UUID   = UUID.fromString("11500004-6215-11ee-8c99-0242ac120002")
    val PUFFIN_CMD_TO: UUID   = UUID.fromString("11500002-6215-11ee-8c99-0242ac120002")
    val PUFFIN_CMD_FROM: UUID = UUID.fromString("11500003-6215-11ee-8c99-0242ac120002")

    // Standard
    val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

    data class StrapProfile(val service: UUID, val data: UUID, val events: UUID, val name: String)

    val ALL_PROFILES = listOf(
        StrapProfile(MAVERICK_SERVICE, MAVERICK_DATA, MAVERICK_EVENTS, "Maverick"),
        StrapProfile(GEN4_SERVICE, GEN4_DATA, GEN4_EVENTS, "Gen4"),
        StrapProfile(PUFFIN_SERVICE, PUFFIN_DATA, PUFFIN_EVENTS, "Puffin"),
    )
}
