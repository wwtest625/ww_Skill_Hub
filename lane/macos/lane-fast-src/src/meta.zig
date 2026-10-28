const std = @import("std");

pub const LANE_META_PATH = "/Users/siri/.agent/lane-meta.json";

pub fn parseHiddenSessions(allocator: std.mem.Allocator, content: []const u8) std.StringHashMap(void) {
    var map = std.StringHashMap(void).init(allocator);
    const parsed = std.json.parseFromSlice(std.json.Value, allocator, content, .{}) catch return map;
    if (parsed.value != .object) return map;
    var it = parsed.value.object.iterator();
    while (it.next()) |entry| {
        const val = entry.value_ptr.*;
        if (val != .object) continue;
        var hidden = false;
        if (val.object.get("deleted")) |d| {
            if (d == .bool) hidden = d.bool;
        }
        if (!hidden) {
            if (val.object.get("archived")) |a| {
                if (a == .bool) hidden = a.bool;
            }
        }
        if (hidden) {
            map.put(allocator.dupe(u8, entry.key_ptr.*) catch continue, {}) catch continue;
        }
    }
    return map;
}

pub fn loadHiddenSessions(allocator: std.mem.Allocator) std.StringHashMap(void) {
    const map = std.StringHashMap(void).init(allocator);
    const f = std.fs.openFileAbsolute(LANE_META_PATH, .{}) catch return map;
    defer f.close();
    const content = f.readToEndAlloc(allocator, 1 << 20) catch return map;
    return parseHiddenSessions(allocator, content);
}

test "parseHiddenSessions hides deleted and archived entries" {
    var arena = std.heap.ArenaAllocator.init(std.testing.allocator);
    defer arena.deinit();
    const allocator = arena.allocator();
    const hidden = parseHiddenSessions(allocator,
        \\{"del":{"deleted":true},"arc":{"archived":true},"keep":{"pinned":true,"deleted":false},"plain":{}}
    );
    try std.testing.expect(hidden.contains("del"));
    try std.testing.expect(hidden.contains("arc"));
    try std.testing.expect(!hidden.contains("keep"));
    try std.testing.expect(!hidden.contains("plain"));
}
