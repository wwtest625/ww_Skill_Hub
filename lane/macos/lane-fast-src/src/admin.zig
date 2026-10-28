const std = @import("std");
const common = @import("common.zig");
const meta = @import("meta.zig");
const c = @import("sqlite.zig");

const AGY_CONV_DIRS = [_][]const u8{
    "/Users/siri/.gemini/antigravity/conversations",
    "/Users/siri/.gemini/antigravity-cli/conversations",
};
const AGY_SUMMARY_DBS = [_][]const u8{
    "/Users/siri/.gemini/antigravity/conversation_summaries.db",
    "/Users/siri/.gemini/antigravity-cli/conversation_summaries.db",
};
const QODER_PROJ_DIR = "/Users/siri/.qoder-cn/projects";
const TRAE_EXPORT_DIR = "/Users/siri/.agent/trae/projects";
const CB_PROJ_DIR = "/Users/siri/.codebuddy/projects";
const CLINE_DATA_DIR = "/Users/siri/.cline/data";
const CLINE_SESSIONS_DB = "/Users/siri/.cline/data/db/sessions.db";
const META_FILE = meta.LANE_META_PATH;
const META_TMP = "/Users/siri/.agent/lane-meta.json.tmp";

const Tm = extern struct {
    tm_sec: c_int,
    tm_min: c_int,
    tm_hour: c_int,
    tm_mday: c_int,
    tm_mon: c_int,
    tm_year: c_int,
    tm_wday: c_int,
    tm_yday: c_int,
    tm_isdst: c_int,
    tm_gmtoff: c_long,
    tm_zone: ?[*:0]const u8,
};

extern "c" fn localtime_r(timer: *const i64, result: *Tm) ?*Tm;

pub const AdminCommand = enum {
    rm,
    restore,
    clean,
    tag,
    pin,
    archive,

    pub fn fromStr(s: []const u8) ?AdminCommand {
        if (std.mem.eql(u8, s, "rm") or std.mem.eql(u8, s, "del") or std.mem.eql(u8, s, "delete")) return .rm;
        if (std.mem.eql(u8, s, "restore") or std.mem.eql(u8, s, "undo")) return .restore;
        if (std.mem.eql(u8, s, "clean") or std.mem.eql(u8, s, "cleanup") or std.mem.eql(u8, s, "gc")) return .clean;
        if (std.mem.eql(u8, s, "tag") or std.mem.eql(u8, s, "tags")) return .tag;
        if (std.mem.eql(u8, s, "pin")) return .pin;
        if (std.mem.eql(u8, s, "archive") or std.mem.eql(u8, s, "arc")) return .archive;
        return null;
    }
};

const Agent = enum {
    agy,
    qoder,
    trae,
    codebuddy,
    cline,

    fn name(self: Agent) []const u8 {
        return switch (self) {
            .agy => "agy",
            .qoder => "qoder",
            .trae => "trae",
            .codebuddy => "codebuddy",
            .cline => "cline",
        };
    }
};

const SessionRef = struct {
    agent: Agent,
    sid: []const u8,
    path: []const u8,
};

fn fail(comptime fmt: []const u8, args: anytype) noreturn {
    const out = std.io.getStdOut().writer();
    out.print(fmt, args) catch {};
    std.process.exit(1);
}

fn nowString(buf: *[32]u8) []const u8 {
    const t: i64 = std.time.timestamp();
    var tm: Tm = undefined;
    if (localtime_r(&t, &tm) == null) return "";
    // Zig 0.14 prints '+' for signed positives when width is set; cast to unsigned.
    const year: u32 = @intCast(tm.tm_year + 1900);
    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2} {d:0>2}:{d:0>2}:{d:0>2}", .{
        year,
        @as(u32, @intCast(tm.tm_mon + 1)),
        @as(u32, @intCast(tm.tm_mday)),
        @as(u32, @intCast(tm.tm_hour)),
        @as(u32, @intCast(tm.tm_min)),
        @as(u32, @intCast(tm.tm_sec)),
    }) catch "";
}

fn emptyMeta(allocator: std.mem.Allocator) std.json.Value {
    return .{ .object = std.json.ObjectMap.init(allocator) };
}

fn loadMetaDoc(allocator: std.mem.Allocator) std.json.Value {
    const f = std.fs.openFileAbsolute(META_FILE, .{}) catch return emptyMeta(allocator);
    defer f.close();
    const content = f.readToEndAlloc(allocator, 1 << 20) catch return emptyMeta(allocator);
    const parsed = std.json.parseFromSlice(std.json.Value, allocator, content, .{}) catch return emptyMeta(allocator);
    if (parsed.value != .object) return emptyMeta(allocator);
    return parsed.value;
}

fn saveMeta(doc: *const std.json.Value, allocator: std.mem.Allocator) !void {
    var buf = std.ArrayList(u8).init(allocator);
    try std.json.stringify(doc.*, .{ .whitespace = .indent_2 }, buf.writer());
    {
        const f = try std.fs.createFileAbsolute(META_TMP, .{});
        defer f.close();
        try f.writeAll(buf.items);
    }
    try std.fs.renameAbsolute(META_TMP, META_FILE);
}

fn ensureEntry(doc: *std.json.Value, allocator: std.mem.Allocator, sid: []const u8) !*std.json.ObjectMap {
    if (doc.* != .object) doc.* = emptyMeta(allocator);
    if (doc.object.getPtr(sid)) |v| {
        if (v.* != .object) v.* = .{ .object = std.json.ObjectMap.init(allocator) };
        return &v.object;
    }
    try doc.object.put(try allocator.dupe(u8, sid), .{ .object = std.json.ObjectMap.init(allocator) });
    return &doc.object.getPtr(sid).?.object;
}

fn setString(entry: *std.json.ObjectMap, allocator: std.mem.Allocator, key: []const u8, val: []const u8) !void {
    try entry.put(key, .{ .string = try allocator.dupe(u8, val) });
}

fn metaBool(doc: *const std.json.Value, sid: []const u8, key: []const u8) bool {
    if (doc.* != .object) return false;
    const v = doc.object.get(sid) orelse return false;
    if (v != .object) return false;
    const b = v.object.get(key) orelse return false;
    if (b != .bool) return false;
    return b.bool;
}

fn removeMetaEntry(doc: *std.json.Value, sid: []const u8) bool {
    if (doc.* != .object) return false;
    return doc.object.swapRemove(sid);
}

fn shortSid(sid: []const u8) []const u8 {
    return if (sid.len >= 8) sid[0..8] else sid;
}

fn addJsonlMatches(
    allocator: std.mem.Allocator,
    root: []const u8,
    agent: Agent,
    needle: []const u8,
    matches: *std.ArrayList(SessionRef),
) !void {
    var dir = std.fs.openDirAbsolute(root, .{ .iterate = true }) catch return;
    defer dir.close();
    var it = dir.iterate();
    while (try it.next()) |proj| {
        if (proj.kind != .directory) continue;
        var pd = dir.openDir(proj.name, .{ .iterate = true }) catch continue;
        defer pd.close();
        var pit = pd.iterate();
        while (try pit.next()) |f| {
            if (f.kind != .file or !std.mem.endsWith(u8, f.name, ".jsonl")) continue;
            const sid = f.name[0 .. f.name.len - 6];
            if (std.mem.indexOf(u8, sid, needle) == null) continue;
            const path = try std.fmt.allocPrint(allocator, "{s}/{s}/{s}", .{ root, proj.name, f.name });
            try matches.append(.{
                .agent = agent,
                .sid = try allocator.dupe(u8, sid),
                .path = path,
            });
        }
    }
}

fn addTraeMatches(allocator: std.mem.Allocator, needle: []const u8, matches: *std.ArrayList(SessionRef)) !void {
    var base = std.fs.openDirAbsolute(TRAE_EXPORT_DIR, .{ .iterate = true }) catch return;
    defer base.close();
    var pit = base.iterate();
    while (try pit.next()) |proj| {
        if (proj.kind != .directory) continue;
        var pdir = base.openDir(proj.name, .{ .iterate = true }) catch continue;
        defer pdir.close();
        var fit = pdir.iterate();
        while (try fit.next()) |f| {
            if (f.kind != .file or !std.mem.endsWith(u8, f.name, ".jsonl")) continue;
            const sid = f.name[0 .. f.name.len - 6];
            if (std.mem.indexOf(u8, sid, needle) == null) continue;
            const path = try std.fmt.allocPrint(allocator, "{s}/{s}/{s}", .{ TRAE_EXPORT_DIR, proj.name, f.name });
            try matches.append(.{
                .agent = .trae,
                .sid = try allocator.dupe(u8, sid),
                .path = path,
            });
        }
    }
}

fn findMatches(allocator: std.mem.Allocator, needle: []const u8) !std.ArrayList(SessionRef) {
    var matches = std.ArrayList(SessionRef).init(allocator);
    if (needle.len == 0) return matches;

    for (AGY_CONV_DIRS) |agy_dir_path| {
        var agy_dir = std.fs.openDirAbsolute(agy_dir_path, .{ .iterate = true }) catch continue;
        defer agy_dir.close();
        var it = agy_dir.iterate();
        while (try it.next()) |f| {
            if (f.kind != .file or !std.mem.endsWith(u8, f.name, ".db")) continue;
            if (std.mem.eql(u8, f.name, "conversation_summaries.db")) continue;
            const sid = f.name[0 .. f.name.len - 3];
            if (std.mem.indexOf(u8, sid, needle) == null) continue;
            try matches.append(.{
                .agent = .agy,
                .sid = try allocator.dupe(u8, sid),
                .path = try std.fmt.allocPrint(allocator, "{s}/{s}", .{ agy_dir_path, f.name }),
            });
        }
    }

    try addJsonlMatches(allocator, QODER_PROJ_DIR, .qoder, needle, &matches);
    try addJsonlMatches(allocator, CB_PROJ_DIR, .codebuddy, needle, &matches);
    try addTraeMatches(allocator, needle, &matches);

    var cline_dir = std.fs.openDirAbsolute(CLINE_DATA_DIR ++ "/sessions", .{ .iterate = true }) catch null;
    if (cline_dir) |*dir| {
        defer dir.close();
        var it = dir.iterate();
        while (try it.next()) |d| {
            if (d.kind != .directory) continue;
            if (std.mem.indexOf(u8, d.name, needle) == null) continue;
            const path = try std.fmt.allocPrint(allocator, "{s}/sessions/{s}/{s}.messages.json", .{ CLINE_DATA_DIR, d.name, d.name });
            try matches.append(.{
                .agent = .cline,
                .sid = try allocator.dupe(u8, d.name),
                .path = path,
            });
        }
    }

    return matches;
}

fn resolveUnique(allocator: std.mem.Allocator, needle: []const u8) ?SessionRef {
    const matches = findMatches(allocator, needle) catch return null;
    if (matches.items.len == 0) return null;
    if (matches.items.len > 1) {
        fail("[-] 匹配到多个会话 ({d} 个)，请提供更详细的会话 ID 前缀。\n", .{matches.items.len});
    }
    return matches.items[0];
}

fn sqliteDeleteRow(allocator: std.mem.Allocator, db_path: []const u8, sql: []const u8, sid: []const u8) void {
    const dbz = allocator.dupeZ(u8, db_path) catch return;
    var db: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(dbz.ptr, &db, c.SQLITE_OPEN_READWRITE, null) != c.SQLITE_OK) {
        if (db) |h| _ = c.sqlite3_close(h);
        return;
    }
    const handle = db orelse return;
    defer _ = c.sqlite3_close(handle);

    const sqlz = allocator.dupeZ(u8, sql) catch return;
    var stmt: ?*c.sqlite3_stmt = null;
    if (c.sqlite3_prepare_v2(handle, sqlz.ptr, -1, &stmt, null) != c.SQLITE_OK) return;
    const s = stmt orelse return;
    defer _ = c.sqlite3_finalize(s);
    _ = c.sqlite3_bind_text(s, 1, sid.ptr, @intCast(sid.len), null);
    _ = c.sqlite3_step(s);
}

fn removeJsonlMatches(allocator: std.mem.Allocator, root: []const u8, sid: []const u8, remove_dirs: bool) void {
    var dir = std.fs.openDirAbsolute(root, .{ .iterate = true }) catch return;
    defer dir.close();
    var it = dir.iterate();
    while (it.next() catch null) |proj| {
        if (proj.kind != .directory) continue;
        var pd = dir.openDir(proj.name, .{ .iterate = true }) catch continue;
        defer pd.close();
        var pit = pd.iterate();
        while (pit.next() catch null) |f| {
            if (!std.mem.startsWith(u8, f.name, sid)) continue;
            const path = std.fmt.allocPrint(allocator, "{s}/{s}/{s}", .{ root, proj.name, f.name }) catch continue;
            if (f.kind == .file) {
                std.fs.deleteFileAbsolute(path) catch {};
            } else if (f.kind == .directory and remove_dirs) {
                std.fs.deleteTreeAbsolute(path) catch {};
            }
        }
    }
}

fn physicalDelete(allocator: std.mem.Allocator, ref: SessionRef) void {
    switch (ref.agent) {
        .agy => {
            std.fs.deleteFileAbsolute(ref.path) catch {};
            for (AGY_SUMMARY_DBS) |summary_db| {
                sqliteDeleteRow(allocator, summary_db, "DELETE FROM conversation_summaries WHERE conversation_id=?", ref.sid);
            }
        },
        .qoder => removeJsonlMatches(allocator, QODER_PROJ_DIR, ref.sid, true),
        .trae => std.fs.deleteFileAbsolute(ref.path) catch {},
        .codebuddy => removeJsonlMatches(allocator, CB_PROJ_DIR, ref.sid, false),
        .cline => {
            if (std.fs.path.dirname(ref.path)) |session_dir| {
                std.fs.deleteTreeAbsolute(session_dir) catch {};
            }
            sqliteDeleteRow(allocator, CLINE_SESSIONS_DB, "DELETE FROM sessions WHERE session_id=?", ref.sid);
        },
    }
}

fn runRm(allocator: std.mem.Allocator, args: []const []const u8) !void {
    var sid_arg: ?[]const u8 = null;
    var force = false;
    for (args) |arg| {
        if (std.mem.eql(u8, arg, "-f") or std.mem.eql(u8, arg, "--force")) {
            force = true;
        } else if (!std.mem.startsWith(u8, arg, "-")) {
            if (sid_arg == null) sid_arg = arg;
        }
    }
    const needle = sid_arg orelse fail("用法: lane rm <会话ID> [-f]\n", .{});
    const ref = resolveUnique(allocator, needle) orelse fail("[-] 未找到会话: {s}\n", .{needle});

    var doc = loadMetaDoc(allocator);
    if (force) {
        physicalDelete(allocator, ref);
        _ = removeMetaEntry(&doc, ref.sid);
        try saveMeta(&doc, allocator);
        const out = std.io.getStdOut().writer();
        try out.print("[+] [{s}] 会话 {s} 已彻底销毁（已清理底层文件）\n", .{ ref.agent.name(), shortSid(ref.sid) });
        return;
    }

    const entry = try ensureEntry(&doc, allocator, ref.sid);
    try entry.put("deleted", .{ .bool = true });
    var tbuf: [32]u8 = undefined;
    try setString(entry, allocator, "deleted_at", nowString(&tbuf));
    try saveMeta(&doc, allocator);
    const out = std.io.getStdOut().writer();
    try out.print("[+] [{s}] 会话 {s} 已移入回收站 🗑️（使用 lane restore {s} 可还原，加 -f 彻底销毁）\n", .{ ref.agent.name(), shortSid(ref.sid), shortSid(ref.sid) });
}

fn runRestore(allocator: std.mem.Allocator, args: []const []const u8) !void {
    if (args.len < 1) fail("用法: lane restore <会话ID>\n", .{});
    const needle = args[0];
    const ref = resolveUnique(allocator, needle) orelse fail("[-] 未找到会话: {s}\n", .{needle});

    var doc = loadMetaDoc(allocator);
    if (doc.object.getPtr(ref.sid)) |v| {
        if (v.* == .object and metaBool(&doc, ref.sid, "deleted")) {
            try v.object.put("deleted", .{ .bool = false });
            _ = v.object.swapRemove("deleted_at");
            try saveMeta(&doc, allocator);
            const out = std.io.getStdOut().writer();
            try out.print("[+] [{s}] 会话 {s} 已从回收站成功还原！\n", .{ ref.agent.name(), shortSid(ref.sid) });
            return;
        }
    }
    fail("[-] 会话 {s} 未处于回收站中。\n", .{shortSid(ref.sid)});
}

fn runPin(allocator: std.mem.Allocator, args: []const []const u8) !void {
    var sid_arg: ?[]const u8 = null;
    var state = true;
    for (args) |arg| {
        if (std.mem.eql(u8, arg, "--off")) {
            state = false;
        } else if (!std.mem.startsWith(u8, arg, "-")) {
            if (sid_arg == null) sid_arg = arg;
        }
    }
    const needle = sid_arg orelse fail("用法: lane pin <会话ID> [--off]\n", .{});
    const ref = resolveUnique(allocator, needle) orelse fail("[-] 未找到会话: {s}\n", .{needle});

    var doc = loadMetaDoc(allocator);
    const entry = try ensureEntry(&doc, allocator, ref.sid);
    try entry.put("pinned", .{ .bool = state });
    var tbuf: [32]u8 = undefined;
    try setString(entry, allocator, "updated_at", nowString(&tbuf));
    try saveMeta(&doc, allocator);
    const out = std.io.getStdOut().writer();
    const status = if (state) "已置顶 🌟" else "已取消置顶";
    try out.print("[+] [{s}] 会话 {s} {s}\n", .{ ref.agent.name(), shortSid(ref.sid), status });
}

fn runArchive(allocator: std.mem.Allocator, args: []const []const u8) !void {
    var sid_arg: ?[]const u8 = null;
    var state = true;
    for (args) |arg| {
        if (std.mem.eql(u8, arg, "--off")) {
            state = false;
        } else if (!std.mem.startsWith(u8, arg, "-")) {
            if (sid_arg == null) sid_arg = arg;
        }
    }
    const needle = sid_arg orelse fail("用法: lane archive <会话ID> [--off]\n", .{});
    const ref = resolveUnique(allocator, needle) orelse fail("[-] 未找到会话: {s}\n", .{needle});

    var doc = loadMetaDoc(allocator);
    const entry = try ensureEntry(&doc, allocator, ref.sid);
    try entry.put("archived", .{ .bool = state });
    var tbuf: [32]u8 = undefined;
    try setString(entry, allocator, "updated_at", nowString(&tbuf));
    try saveMeta(&doc, allocator);
    const out = std.io.getStdOut().writer();
    const status = if (state) "已归档 📦" else "已移出归档";
    try out.print("[+] [{s}] 会话 {s} {s}\n", .{ ref.agent.name(), shortSid(ref.sid), status });
}

fn readTags(entry: *std.json.ObjectMap, allocator: std.mem.Allocator) !std.ArrayList([]const u8) {
    var tags = std.ArrayList([]const u8).init(allocator);
    const v = entry.get("tags") orelse return tags;
    if (v != .array) return tags;
    for (v.array.items) |item| {
        if (item == .string) try tags.append(item.string);
    }
    return tags;
}

fn tagsJson(tags: []const []const u8, allocator: std.mem.Allocator) ![]const u8 {
    var buf = std.ArrayList(u8).init(allocator);
    const out = buf.writer();
    try out.writeByte('[');
    for (tags, 0..) |t, i| {
        if (i > 0) try out.writeAll(", ");
        try std.json.stringify(t, .{}, out);
    }
    try out.writeByte(']');
    return buf.items;
}

fn runTag(allocator: std.mem.Allocator, args: []const []const u8) !void {
    if (args.len < 2) fail("用法: lane tag <会话ID> add|rm|list [标签...]\n", .{});
    const needle = args[0];
    const action = args[1];
    const ref = resolveUnique(allocator, needle) orelse fail("[-] 未找到会话: {s}\n", .{needle});

    var doc = loadMetaDoc(allocator);
    if (std.mem.eql(u8, action, "list")) {
        const out = std.io.getStdOut().writer();
        if (doc.object.getPtr(ref.sid)) |v| {
            if (v.* == .object) {
                const tags = try readTags(&v.object, allocator);
                try out.print("{s}\n", .{try tagsJson(tags.items, allocator)});
                return;
            }
        }
        try out.print("[]\n", .{});
        return;
    }
    if (!std.mem.eql(u8, action, "add") and !std.mem.eql(u8, action, "rm")) {
        fail("用法: lane tag <会话ID> add|rm|list [标签...]\n", .{});
    }

    const entry = try ensureEntry(&doc, allocator, ref.sid);
    var tags = try readTags(entry, allocator);

    for (args[2..]) |raw| {
        const t = std.mem.trim(u8, raw, " \t\r\n");
        if (t.len == 0) continue;
        if (std.mem.eql(u8, action, "add")) {
            var found = false;
            for (tags.items) |existing| {
                if (std.mem.eql(u8, existing, t)) {
                    found = true;
                    break;
                }
            }
            if (!found) try tags.append(t);
        } else {
            var i: usize = 0;
            while (i < tags.items.len) {
                if (std.mem.eql(u8, tags.items[i], t)) {
                    _ = tags.orderedRemove(i);
                } else {
                    i += 1;
                }
            }
        }
    }

    std.mem.sort([]const u8, tags.items, {}, struct {
        fn lessThan(_: void, a: []const u8, b: []const u8) bool {
            return std.mem.lessThan(u8, a, b);
        }
    }.lessThan);

    var arr = std.json.Array.init(allocator);
    for (tags.items) |t| try arr.append(.{ .string = t });
    try entry.put("tags", .{ .array = arr });
    var tbuf: [32]u8 = undefined;
    try setString(entry, allocator, "updated_at", nowString(&tbuf));
    try saveMeta(&doc, allocator);

    const out = std.io.getStdOut().writer();
    try out.print("[+] [{s}] 会话 {s} 当前标签: {s}\n", .{ ref.agent.name(), shortSid(ref.sid), try tagsJson(tags.items, allocator) });
}

const CleanOptions = struct {
    empty: bool = false,
    trash: bool = false,
    dry_run: bool = false,
    force: bool = false,
    max_kb: usize = 20,
};

fn parseCleanOptions(args: []const []const u8) CleanOptions {
    var opts = CleanOptions{};
    var i: usize = 0;
    while (i < args.len) : (i += 1) {
        const arg = args[i];
        if (std.mem.eql(u8, arg, "--empty")) {
            opts.empty = true;
        } else if (std.mem.eql(u8, arg, "--trash")) {
            opts.trash = true;
        } else if (std.mem.eql(u8, arg, "--dry-run")) {
            opts.dry_run = true;
        } else if (std.mem.eql(u8, arg, "-f") or std.mem.eql(u8, arg, "--force")) {
            opts.force = true;
        } else if (std.mem.eql(u8, arg, "--max-kb")) {
            i += 1;
            if (i < args.len) {
                opts.max_kb = std.fmt.parseInt(usize, args[i], 10) catch 20;
            }
        } else if (!std.mem.startsWith(u8, arg, "-")) {
            fail("未知参数: {s}（用法: lane clean --empty|--trash [--max-kb N] [--dry-run] [-f]）\n", .{arg});
        }
    }
    return opts;
}

const Candidate = struct {
    ref: SessionRef,
    size: u64,
    title: []const u8,
    reason: []const u8,
};

fn fileSize(path: []const u8) ?u64 {
    const f = std.fs.openFileAbsolute(path, .{}) catch return null;
    defer f.close();
    const st = f.stat() catch return null;
    return st.size;
}

fn formatSize(bytes: u64, buf: []u8) []const u8 {
    if (bytes < 1024) return std.fmt.bufPrint(buf, "{d}B", .{bytes}) catch "-";
    if (bytes < 1024 * 1024) {
        const kb = @as(f64, @floatFromInt(bytes)) / 1024.0;
        return std.fmt.bufPrint(buf, "{d:.1}K", .{kb}) catch "-";
    }
    const mb = @as(f64, @floatFromInt(bytes)) / (1024.0 * 1024.0);
    return std.fmt.bufPrint(buf, "{d:.1}M", .{mb}) catch "-";
}

const Summary = struct {
    preview: []const u8,
    steps: c_int,
};

fn loadAgySummaries(allocator: std.mem.Allocator) std.StringHashMap(Summary) {
    var map = std.StringHashMap(Summary).init(allocator);
    for (AGY_SUMMARY_DBS) |summary_db| {
        const dbz = allocator.dupeZ(u8, summary_db) catch continue;
        defer allocator.free(dbz);
        var db: ?*c.sqlite3 = null;
        if (c.sqlite3_open_v2(dbz.ptr, &db, c.SQLITE_OPEN_READONLY, null) != c.SQLITE_OK) {
            if (db) |h| _ = c.sqlite3_close(h);
            continue;
        }
        const handle = db orelse continue;
        defer _ = c.sqlite3_close(handle);

        var stmt: ?*c.sqlite3_stmt = null;
        const sql = "SELECT conversation_id, preview, step_count FROM conversation_summaries";
        if (c.sqlite3_prepare_v2(handle, sql, -1, &stmt, null) != c.SQLITE_OK) continue;
        const s = stmt orelse continue;
        defer _ = c.sqlite3_finalize(s);

        while (c.sqlite3_step(s) == c.SQLITE_ROW) {
            const cid_c = c.sqlite3_column_text(s, 0) orelse continue;
            const cid = std.mem.span(cid_c);
            const prev_c = c.sqlite3_column_text(s, 1);
            const prev = if (prev_c) |p| std.mem.span(p) else "";
            const steps = c.sqlite3_column_int(s, 2);
            map.put(
                allocator.dupe(u8, cid) catch continue,
                .{ .preview = allocator.dupe(u8, prev) catch continue, .steps = steps },
            ) catch continue;
        }
    }
    return map;
}

fn collectCandidates(allocator: std.mem.Allocator, opts: CleanOptions) !std.ArrayList(Candidate) {
    var list = std.ArrayList(Candidate).init(allocator);
    const doc = loadMetaDoc(allocator);
    const max_bytes = opts.max_kb * 1024;

    const summaries = loadAgySummaries(allocator);
    for (AGY_CONV_DIRS) |agy_dir_path| {
        var agy_dir = std.fs.openDirAbsolute(agy_dir_path, .{ .iterate = true }) catch continue;
        defer agy_dir.close();
        var it = agy_dir.iterate();
        while (try it.next()) |f| {
            if (f.kind != .file or !std.mem.endsWith(u8, f.name, ".db")) continue;
            if (std.mem.eql(u8, f.name, "conversation_summaries.db")) continue;
            const sid = f.name[0 .. f.name.len - 3];
            if (metaBool(&doc, sid, "pinned")) continue;
            const path = try std.fmt.allocPrint(allocator, "{s}/{s}", .{ agy_dir_path, f.name });
            const size = fileSize(path) orelse continue;
            var title: []const u8 = "(空会话)";
            if (summaries.get(sid)) |sum| {
                if (sum.preview.len > 0) title = sum.preview;
            }
            const noisy = common.isNoisyTitle(title);
            const small = size < max_bytes;
            if (!noisy and !small) continue;
            const reason = if (noisy) "空会话/指令" else try std.fmt.allocPrint(allocator, "体积<{d}KB", .{opts.max_kb});
            try list.append(.{
                .ref = .{ .agent = .agy, .sid = try allocator.dupe(u8, sid), .path = path },
                .size = size,
                .title = title,
                .reason = reason,
            });
        }
    }

    const roots = [_]struct { path: []const u8, agent: Agent }{
        .{ .path = QODER_PROJ_DIR, .agent = .qoder },
        .{ .path = CB_PROJ_DIR, .agent = .codebuddy },
    };
    for (roots) |root| {
        var dir = std.fs.openDirAbsolute(root.path, .{ .iterate = true }) catch continue;
        defer dir.close();
        var it = dir.iterate();
        while (try it.next()) |proj| {
            if (proj.kind != .directory) continue;
            var pd = dir.openDir(proj.name, .{ .iterate = true }) catch continue;
            defer pd.close();
            var pit = pd.iterate();
            while (try pit.next()) |f| {
                if (f.kind != .file or !std.mem.endsWith(u8, f.name, ".jsonl")) continue;
                const sid = f.name[0 .. f.name.len - 6];
                if (metaBool(&doc, sid, "pinned")) continue;
                const path = try std.fmt.allocPrint(allocator, "{s}/{s}/{s}", .{ root.path, proj.name, f.name });
                const size = fileSize(path) orelse continue;
                const title = common.extractJsonlTitle(path, allocator) catch "(空会话)";
                const noisy = common.isNoisyTitle(title);
                const small = size < max_bytes;
                if (!noisy and !small) continue;
                const reason = if (noisy) "空会话/指令" else try std.fmt.allocPrint(allocator, "体积<{d}KB", .{opts.max_kb});
                try list.append(.{
                    .ref = .{ .agent = root.agent, .sid = try allocator.dupe(u8, sid), .path = path },
                    .size = size,
                    .title = title,
                    .reason = reason,
                });
            }
        }
    }

    var cline_dir = std.fs.openDirAbsolute(CLINE_DATA_DIR ++ "/sessions", .{ .iterate = true }) catch null;
    if (cline_dir) |*dir| {
        defer dir.close();
        var it = dir.iterate();
        while (try it.next()) |d| {
            if (d.kind != .directory) continue;
            if (metaBool(&doc, d.name, "pinned")) continue;
            const path = try std.fmt.allocPrint(allocator, "{s}/sessions/{s}/{s}.messages.json", .{ CLINE_DATA_DIR, d.name, d.name });
            const size = fileSize(path) orelse continue;
            if (size >= max_bytes) continue;
            try list.append(.{
                .ref = .{ .agent = .cline, .sid = try allocator.dupe(u8, d.name), .path = path },
                .size = size,
                .title = "(cline 会话)",
                .reason = try std.fmt.allocPrint(allocator, "体积<{d}KB", .{opts.max_kb}),
            });
        }
    }

    return list;
}

fn runCleanEmpty(allocator: std.mem.Allocator, opts: CleanOptions) !void {
    const candidates = try collectCandidates(allocator, opts);
    const out = std.io.getStdOut().writer();
    if (candidates.items.len == 0) {
        try out.print("[*] 太棒了！当前没有任何无用、空会话或小于 {d}KB 的残留会话。\n", .{opts.max_kb});
        return;
    }

    const mode = if (opts.dry_run) "[预览模式]" else if (opts.force) "[彻底销毁模式]" else "[回收站软删除模式]";
    try out.print("[*] 发现 {d} 个无用/空/小于{d}KB 会话 {s}:\n", .{ candidates.items.len, opts.max_kb, mode });
    try out.print("{s}\n", .{"-" ** 110});
    var sz_buf: [32]u8 = undefined;
    for (candidates.items) |cand| {
        try out.print("  - [{s: <9}] {s: <18} {s: <8} {s: <22} ({s}) ID: {s}\n", .{
            cand.ref.agent.name(),
            "-",
            formatSize(cand.size, &sz_buf),
            common.truncateUtf8Safe(cand.title, 22),
            cand.reason,
            cand.ref.sid,
        });
    }
    try out.print("{s}\n", .{"-" ** 110});

    if (opts.dry_run) {
        try out.print("[*] 预览完毕，共 {d} 条记录待清理。去掉 --dry-run 即可执行（默认为软删除，加 -f 彻底销毁）。\n", .{candidates.items.len});
        return;
    }

    var doc = loadMetaDoc(allocator);
    var count: usize = 0;
    for (candidates.items) |cand| {
        if (opts.force) {
            physicalDelete(allocator, cand.ref);
            _ = removeMetaEntry(&doc, cand.ref.sid);
            try out.print("[+] [{s}] 会话 {s} 已彻底销毁\n", .{ cand.ref.agent.name(), shortSid(cand.ref.sid) });
        } else {
            const entry = try ensureEntry(&doc, allocator, cand.ref.sid);
            try entry.put("deleted", .{ .bool = true });
            var tbuf: [32]u8 = undefined;
            try setString(entry, allocator, "deleted_at", nowString(&tbuf));
            try out.print("[+] [{s}] 会话 {s} 已移入回收站 🗑️（使用 lane restore {s} 可还原，加 -f 彻底销毁）\n", .{ cand.ref.agent.name(), shortSid(cand.ref.sid), shortSid(cand.ref.sid) });
        }
        count += 1;
    }
    try saveMeta(&doc, allocator);
    try out.print("[+] 清理完成！共处理 {d} 个垃圾/微型会话。\n", .{count});
}

fn runCleanTrash(allocator: std.mem.Allocator, opts: CleanOptions) !void {
    var doc = loadMetaDoc(allocator);
    var trash_sids = std.ArrayList([]const u8).init(allocator);
    var it = doc.object.iterator();
    while (it.next()) |entry| {
        const v = entry.value_ptr.*;
        if (v != .object) continue;
        const d = v.object.get("deleted") orelse continue;
        if (d != .bool or !d.bool) continue;
        try trash_sids.append(try allocator.dupe(u8, entry.key_ptr.*));
    }

    const out = std.io.getStdOut().writer();
    if (trash_sids.items.len == 0) {
        try out.print("[*] 回收站为空，没有需要清理的会话。\n", .{});
        return;
    }

    try out.print("[*] 回收站中共有 {d} 个会话待彻底销毁:\n", .{trash_sids.items.len});
    for (trash_sids.items) |sid| {
        var title: []const u8 = "-";
        if (doc.object.getPtr(sid)) |v| {
            if (v.* == .object) {
                if (v.object.get("title")) |t| {
                    if (t == .string) title = t.string;
                }
            }
        }
        try out.print("  - ID: {s} | 标题: {s}\n", .{ sid, title });
    }

    if (opts.dry_run) {
        try out.print("[*] 预览模式，未执行物理删除。去掉 --dry-run 即可彻底粉碎。\n", .{});
        return;
    }

    var cleaned: usize = 0;
    for (trash_sids.items) |sid| {
        const matches = try findMatches(allocator, sid);
        if (matches.items.len == 0) {
            try out.print("[-] [{s}] 底层文件已不存在，移除元数据\n", .{shortSid(sid)});
        } else {
            for (matches.items) |ref| {
                physicalDelete(allocator, ref);
                try out.print("[+] [{s}] 会话 {s} 已彻底销毁\n", .{ ref.agent.name(), shortSid(ref.sid) });
            }
        }
        _ = removeMetaEntry(&doc, sid);
        cleaned += 1;
    }
    try saveMeta(&doc, allocator);
    try out.print("[+] 回收站已彻底清空！共物理销毁 {d} 个会话。\n", .{cleaned});
}

pub fn run(cmd: AdminCommand, args: []const []const u8, allocator: std.mem.Allocator) !void {
    switch (cmd) {
        .rm => try runRm(allocator, args),
        .restore => try runRestore(allocator, args),
        .clean => {
            const opts = parseCleanOptions(args);
            if (opts.trash) {
                try runCleanTrash(allocator, opts);
            } else if (opts.empty) {
                try runCleanEmpty(allocator, opts);
            } else {
                fail("用法: lane clean --empty|--trash [--max-kb N] [--dry-run] [-f]\n", .{});
            }
        },
        .tag => try runTag(allocator, args),
        .pin => try runPin(allocator, args),
        .archive => try runArchive(allocator, args),
    }
}
