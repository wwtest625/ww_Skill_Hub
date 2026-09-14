const std = @import("std");

pub const c = struct {
    pub const SQLITE_OK = 0;
    pub const SQLITE_ROW = 100;
    pub const SQLITE_DONE = 101;
    pub const SQLITE_OPEN_READONLY = 0x00000001;

    pub const sqlite3 = opaque {};
    pub const sqlite3_stmt = opaque {};

    pub extern "c" fn sqlite3_open_v2(filename: [*:0]const u8, ppDb: *?*sqlite3, flags: c_int, zVfs: ?[*:0]const u8) c_int;
    pub extern "c" fn sqlite3_prepare_v2(db: *sqlite3, zSql: [*:0]const u8, nByte: c_int, ppStmt: *?*sqlite3_stmt, pzTail: ?*[*:0]const u8) c_int;
    pub extern "c" fn sqlite3_step(stmt: *sqlite3_stmt) c_int;
    pub extern "c" fn sqlite3_column_text(stmt: *sqlite3_stmt, iCol: c_int) ?[*:0]const u8;
    pub extern "c" fn sqlite3_column_blob(stmt: *sqlite3_stmt, iCol: c_int) ?[*]const u8;
    pub extern "c" fn sqlite3_column_bytes(stmt: *sqlite3_stmt, iCol: c_int) c_int;
    pub extern "c" fn sqlite3_column_int(stmt: *sqlite3_stmt, iCol: c_int) c_int;
    pub extern "c" fn sqlite3_column_int64(stmt: *sqlite3_stmt, iCol: c_int) i64;
    pub extern "c" fn sqlite3_bind_text(stmt: *sqlite3_stmt, iCol: c_int, zData: [*]const u8, nData: c_int, xDel: ?*const anyopaque) c_int;
    pub extern "c" fn sqlite3_finalize(stmt: *sqlite3_stmt) c_int;
    pub extern "c" fn sqlite3_close(db: *sqlite3) c_int;
};

pub const AgentType = enum {
    all,
    agy,
    qoder,
    codebuddy,
    reasonix,

    pub fn fromStr(s: []const u8) AgentType {
        if (std.ascii.eqlIgnoreCase(s, "agy")) return .agy;
        if (std.ascii.eqlIgnoreCase(s, "qoder")) return .qoder;
        if (std.ascii.eqlIgnoreCase(s, "codebuddy") or std.ascii.eqlIgnoreCase(s, "cb")) return .codebuddy;
        if (std.ascii.eqlIgnoreCase(s, "reasonix") or std.ascii.eqlIgnoreCase(s, "rea")) return .reasonix;
        return .all;
    }
};

pub const SessionItem = struct {
    agent: []const u8,
    sid: []const u8,
    mtime: i128,
    time_str: []const u8,
    ws: []const u8,
    size_bytes: u64,
    title: []const u8,
    path: []const u8,

    pub fn formatSize(bytes: u64, buf: []u8) []const u8 {
        if (bytes < 1024) {
            return std.fmt.bufPrint(buf, "{d}B", .{bytes}) catch "-";
        } else if (bytes < 1024 * 1024) {
            const kb = @as(f64, @floatFromInt(bytes)) / 1024.0;
            return std.fmt.bufPrint(buf, "{d:.1}K", .{kb}) catch "-";
        } else {
            const mb = @as(f64, @floatFromInt(bytes)) / (1024.0 * 1024.0);
            return std.fmt.bufPrint(buf, "{d:.1}M", .{mb}) catch "-";
        }
    }
};

pub const LaneCard = struct {
    session_id: []const u8,
    action: []const u8,
    conclusion: []const u8,
    status: []const u8,
    entities_json: []const u8,

    pub fn statusIcon(status_str: []const u8) []const u8 {
        if (std.mem.eql(u8, status_str, "done")) return "✅";
        if (std.mem.eql(u8, status_str, "ongoing")) return "🔄";
        if (std.mem.eql(u8, status_str, "aborted")) return "❌";
        return "·";
    }
};

fn loadLatestLaneCards(allocator: std.mem.Allocator) std.StringHashMap(LaneCard) {
    var map = std.StringHashMap(LaneCard).init(allocator);
    var db_ptr: ?*c.sqlite3 = null;
    const rc = c.sqlite3_open_v2("/root/.agent/lane-summary.db", &db_ptr, c.SQLITE_OPEN_READONLY, null);
    if (db_ptr) |db| {
        defer _ = c.sqlite3_close(db);
        if (rc == c.SQLITE_OK) {
            var stmt: ?*c.sqlite3_stmt = null;
            const sql = "SELECT session_id, action, conclusion, status, entities_json FROM lane_cards ORDER BY id ASC;";
            if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
                if (stmt) |s| {
                    defer _ = c.sqlite3_finalize(s);
                    while (c.sqlite3_step(s) == c.SQLITE_ROW) {
                        const sid_c = c.sqlite3_column_text(s, 0);
                        const act_c = c.sqlite3_column_text(s, 1);
                        const conc_c = c.sqlite3_column_text(s, 2);
                        const stat_c = c.sqlite3_column_text(s, 3);
                        const ent_c = c.sqlite3_column_text(s, 4);

                        if (sid_c) |sc| {
                            const sid_str = std.mem.span(sc);
                            const act_str = if (act_c) |ac| std.mem.span(ac) else "";
                            const conc_str = if (conc_c) |cc| std.mem.span(cc) else "";
                            const stat_str = if (stat_c) |stc| std.mem.span(stc) else "done";
                            const ent_str = if (ent_c) |ec| std.mem.span(ec) else "[]";

                            map.put(
                                allocator.dupe(u8, sid_str) catch continue,
                                .{
                                    .session_id = allocator.dupe(u8, sid_str) catch continue,
                                    .action = allocator.dupe(u8, act_str) catch continue,
                                    .conclusion = allocator.dupe(u8, conc_str) catch continue,
                                    .status = allocator.dupe(u8, stat_str) catch continue,
                                    .entities_json = allocator.dupe(u8, ent_str) catch continue,
                                },
                            ) catch continue;
                        }
                    }
                }
            }
        }
    }
    return map;
}

fn printSessionCards(sid: []const u8) void {
    var db_ptr: ?*c.sqlite3 = null;
    const rc = c.sqlite3_open_v2("/root/.agent/lane-summary.db", &db_ptr, c.SQLITE_OPEN_READONLY, null);
    if (db_ptr) |db| {
        defer _ = c.sqlite3_close(db);
        if (rc == c.SQLITE_OK) {
            var stmt: ?*c.sqlite3_stmt = null;
            const sql = "SELECT id, action, conclusion, status, entities_json, created_at FROM lane_cards WHERE session_id = ? ORDER BY id ASC;";
            if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
                if (stmt) |s| {
                    defer _ = c.sqlite3_finalize(s);
                    _ = c.sqlite3_bind_text(s, 1, sid.ptr, @intCast(sid.len), null);

                    const stdout = std.io.getStdOut().writer();
                    var card_idx: usize = 0;
                    while (c.sqlite3_step(s) == c.SQLITE_ROW) {
                        card_idx += 1;
                        if (card_idx == 1) {
                            stdout.print("\x1b[1;33m=== 里程碑卡片序列 (按时间追加) ===\x1b[0m\n", .{}) catch {};
                        }
                        const act_c = c.sqlite3_column_text(s, 1);
                        const conc_c = c.sqlite3_column_text(s, 2);
                        const stat_c = c.sqlite3_column_text(s, 3);
                        const ent_c = c.sqlite3_column_text(s, 4);

                        const act_str = if (act_c) |ac| std.mem.span(ac) else "";
                        const conc_str = if (conc_c) |cc| std.mem.span(cc) else "";
                        const stat_str = if (stat_c) |stc| std.mem.span(stc) else "done";
                        const ent_str = if (ent_c) |ec| std.mem.span(ec) else "[]";
                        const icon = LaneCard.statusIcon(stat_str);

                        stdout.print("\x1b[1;36m[卡片 #{d}]\x1b[0m {s} 状态: \x1b[1m{s}\x1b[0m | 实体: \x1b[90m{s}\x1b[0m\n", .{ card_idx, icon, stat_str, ent_str }) catch {};
                        stdout.print("  - 干了啥: {s}\n", .{act_str}) catch {};
                        stdout.print("  - 结  论: {s}\n\n", .{conc_str}) catch {};
                    }
                    if (card_idx > 0) {
                        stdout.print("\x1b[90m--------------------------------------------------\x1b[0m\n\n", .{}) catch {};
                    }
                }
            }
        }
    }
}

fn truncateUtf8Safe(text: []const u8, max_chars: usize) []const u8 {
    var count: usize = 0;
    var byte_idx: usize = 0;
    while (byte_idx < text.len and count < max_chars) {
        const len = std.unicode.utf8ByteSequenceLength(text[byte_idx]) catch 1;
        if (byte_idx + len > text.len) break;
        byte_idx += len;
        count += 1;
    }
    return text[0..byte_idx];
}

fn formatEpochSeconds(epoch_sec: i64, buf: []u8) []const u8 {
    if (epoch_sec <= 0) return "1970-01-01 00:00";
    const beijing_sec = epoch_sec + 8 * 3600;
    const epoch_day = @divFloor(beijing_sec, 86400);
    const day_sec = @mod(beijing_sec, 86400);

    const hour: u32 = @intCast(@divFloor(day_sec, 3600));
    const minute: u32 = @intCast(@divFloor(@mod(day_sec, 3600), 60));

    const z = epoch_day + 719468;
    const era = @divFloor(if (z >= 0) z else z - 146096, 146097);
    const doe = @as(u64, @intCast(z - era * 146097));
    const yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    const y = @as(i64, @intCast(yoe)) + era * 400;
    const doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const mp = (5 * doy + 2) / 153;
    const d: u32 = @intCast(doy - (153 * mp + 2) / 5 + 1);
    const m: u32 = @intCast(if (mp < 10) mp + 3 else mp - 9);
    const year: u32 = @intCast(if (m <= 2) y + 1 else y);

    return std.fmt.bufPrint(buf, "{d:0>4}-{d:0>2}-{d:0>2} {d:0>2}:{d:0>2}", .{ year, m, d, hour, minute }) catch "----";
}

fn cleanWorkspace(raw: []const u8) []const u8 {
    var ws = std.mem.trim(u8, raw, " \t\r\n\"'[]");
    if (std.mem.startsWith(u8, ws, "file://")) ws = ws[7..];
    if (std.mem.startsWith(u8, ws, "-")) ws = ws[1..];
    if (std.mem.eql(u8, ws, "root") or std.mem.eql(u8, ws, "/root") or std.mem.eql(u8, ws, "/root/")) {
        return "root";
    }
    if (std.mem.startsWith(u8, ws, "root-")) return ws[5..];
    if (std.mem.startsWith(u8, ws, "/root/")) return ws[6..];
    return if (ws.len > 0) ws else "root";
}

/// 读文件尾部最多 buf.len 字节（用于解析 change session 切换痕迹）
fn readFileTail(path: []const u8, buf: []u8) ?[]const u8 {
    const f = std.fs.openFileAbsolute(path, .{}) catch return null;
    defer f.close();
    const size = f.getEndPos() catch return null;
    const start = if (size > buf.len) size - buf.len else 0;
    f.seekTo(start) catch return null;
    const n = f.read(buf) catch return null;
    return buf[0..n];
}

/// 文件最后一条消息是否为 change session 切换控制行
fn lastLineIsChangeSession(tail: []const u8) bool {
    const trimmed = std.mem.trimRight(u8, tail, " \t\r\n");
    if (trimmed.len == 0) return false;
    const line_start = std.mem.lastIndexOfScalar(u8, trimmed, '\n') orelse 0;
    const line = trimmed[line_start..];
    return std.mem.indexOf(u8, line, "<local-command-stdout>") != null and
        std.mem.indexOf(u8, line, "change session ") != null;
}

/// 从尾部提取最后一个 change session 的目标会话 UUID（36 字符）
fn extractChangeTarget(tail: []const u8, out: []u8) ?[]const u8 {
    const marker = "change session ";
    const idx = std.mem.lastIndexOf(u8, tail, marker) orelse return null;
    const i = idx + marker.len;
    if (i + 36 > tail.len) return null;
    const cand = tail[i .. i + 36];
    for (cand) |ch| {
        if (!(std.ascii.isHex(ch) or ch == '-')) return null;
    }
    @memcpy(out[0..36], cand);
    return out[0..36];
}

/// 尾部最后一条"实质消息"的 timestamp (ms)：逐行倒序，
/// 跳过 change session 切换痕迹与 command-caveat 控制行
fn lastRealTimestampMs(tail: []const u8) ?i64 {
    var iter = std.mem.splitBackwardsScalar(u8, tail, '\n');
    while (iter.next()) |line| {
        if (line.len < 20) continue;
        if (std.mem.indexOf(u8, line, "<local-command-stdout>") != null) continue;
        if (std.mem.indexOf(u8, line, "command-caveat") != null) continue;
        const key = "\"timestamp\":";
        const tidx = std.mem.indexOf(u8, line, key) orelse continue;
        var e = tidx + key.len;
        while (e < line.len and line[e] >= '0' and line[e] <= '9') : (e += 1) {}
        return std.fmt.parseInt(i64, line[tidx + key.len .. e], 10) catch continue;
    }
    return null;
}

/// 极速内存切片大小写不敏感匹配：首字符双 case 预筛，杜绝无效全量 toLower 比较
pub fn sliceContainsInsensitive(haystack: []const u8, kw: []const u8) bool {
    if (kw.len == 0) return true;
    if (haystack.len < kw.len) return false;

    const first_low = std.ascii.toLower(kw[0]);
    const first_up = std.ascii.toUpper(kw[0]);
    const kw_rest = kw[1..];
    const limit = haystack.len - kw.len;

    var i: usize = 0;
    if (first_low == first_up) {
        while (i <= limit) {
            if (std.mem.indexOfScalar(u8, haystack[i .. limit + 1], first_low)) |rel_idx| {
                const match_idx = i + rel_idx;
                var matched = true;
                for (kw_rest, 0..) |target_ch, k| {
                    if (std.ascii.toLower(haystack[match_idx + 1 + k]) != std.ascii.toLower(target_ch)) {
                        matched = false;
                        break;
                    }
                }
                if (matched) return true;
                i = match_idx + 1;
            } else {
                break;
            }
        }
    } else {
        while (i <= limit) : (i += 1) {
            const b = haystack[i];
            if (b == first_low or b == first_up) {
                var matched = true;
                for (kw_rest, 0..) |target_ch, k| {
                    if (std.ascii.toLower(haystack[i + 1 + k]) != std.ascii.toLower(target_ch)) {
                        matched = false;
                        break;
                    }
                }
                if (matched) return true;
            }
        }
    }
    return false;
}

/// 支持 256KB 大缓冲与无缝跨 chunk 滑动窗口的全文搜索，极大减少 syscall 且杜绝边界假阴性
fn fileContainsInsensitive(path: []const u8, kw: []const u8) bool {
    const file = std.fs.openFileAbsolute(path, .{}) catch return false;
    defer file.close();

    if (kw.len == 0) return true;
    var buf: [256 * 1024]u8 = undefined;
    if (kw.len >= buf.len) return false;
    var overlap_len: usize = 0;

    while (true) {
        const read_n = file.read(buf[overlap_len..]) catch break;
        if (read_n == 0) break;
        const total = overlap_len + read_n;
        const chunk = buf[0..total];

        if (sliceContainsInsensitive(chunk, kw)) return true;

        if (kw.len > 1 and total >= kw.len - 1) {
            overlap_len = kw.len - 1;
            std.mem.copyForwards(u8, buf[0..overlap_len], chunk[total - overlap_len .. total]);
        } else {
            overlap_len = 0;
        }
    }
    return false;
}

/// agy SQLite 数据库精准 payload 级全文检索：只检索 steps.step_payload 二进制切片，跳过页头、元数据与 freelist 噪声
fn sqliteDbContainsInsensitive(db_path_z: [*:0]const u8, kw: []const u8) bool {
    var db_ptr: ?*c.sqlite3 = null;
    if (c.sqlite3_open_v2(db_path_z, &db_ptr, c.SQLITE_OPEN_READONLY, null) != c.SQLITE_OK) {
        if (db_ptr) |db| _ = c.sqlite3_close(db);
        return false;
    }
    const db = db_ptr.?;
    defer _ = c.sqlite3_close(db);

    var stmt: ?*c.sqlite3_stmt = null;
    const sql = "SELECT step_payload FROM steps;";
    if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) != c.SQLITE_OK) {
        if (stmt) |s| _ = c.sqlite3_finalize(s);
        return false;
    }
    const s = stmt.?;
    defer _ = c.sqlite3_finalize(s);

    while (c.sqlite3_step(s) == c.SQLITE_ROW) {
        const blob_ptr = c.sqlite3_column_blob(s, 0);
        const bytes = c.sqlite3_column_bytes(s, 0);
        if (blob_ptr != null and bytes > 0) {
            const blob_len = @as(usize, @intCast(bytes));
            const blob = blob_ptr.?[0..blob_len];
            if (sliceContainsInsensitive(blob, kw)) {
                return true;
            }
        }
    }
    return false;
}

fn extractJsonlTitle(path: []const u8, allocator: std.mem.Allocator) ![]const u8 {
    const file = try std.fs.openFileAbsolute(path, .{});
    defer file.close();

    var buf: [65536]u8 = undefined;
    const n = try file.read(&buf);
    const content = buf[0..n];

    if (std.mem.indexOf(u8, content, "\"aiTitle\":\"")) |idx| {
        const start = idx + 11;
        if (std.mem.indexOfScalar(u8, content[start..], '"')) |end_rel| {
            const title = content[start .. start + end_rel];
            if (title.len > 0) {
                return try allocator.dupe(u8, truncateUtf8Safe(title, 36));
            }
        }
    }

    var iter = std.mem.splitScalar(u8, content, '\n');
    while (iter.next()) |line| {
        if (line.len < 10) continue;
        if (std.mem.indexOf(u8, line, "\"command-name\">")) |cmd_idx| {
            const start = cmd_idx + 15;
            if (std.mem.indexOf(u8, line[start..], "</command-name>")) |cmd_end| {
                const cmd = line[start .. start + cmd_end];
                return try std.fmt.allocPrint(allocator, "({s})", .{cmd});
            }
        }

        if (std.mem.indexOf(u8, line, "\"role\":\"user\"") != null or
            std.mem.indexOf(u8, line, "\"type\":\"user\"") != null or
            std.mem.indexOf(u8, line, "\"type\":\"USER_INPUT\"") != null)
        {
            if (std.mem.indexOf(u8, line, "\"text\":\"")) |t_idx| {
                const start = t_idx + 8;
                if (std.mem.indexOfScalar(u8, line[start..], '"')) |t_end| {
                    const text = line[start .. start + t_end];
                    if (!std.mem.startsWith(u8, text, "<system-") and
                        !std.mem.startsWith(u8, text, "<local-"))
                    {
                        return try allocator.dupe(u8, truncateUtf8Safe(text, 36));
                    }
                }
            }
            if (std.mem.indexOf(u8, line, "\"content\":\"")) |c_idx| {
                const start = c_idx + 11;
                var end = start;
                while (end < line.len) : (end += 1) {
                    if (line[end] == '"' and line[end - 1] != '\\') break;
                }
                var text = line[start..end];
                if (std.mem.indexOf(u8, text, "<USER_REQUEST>\\n")) |req_start| {
                    const real_start = req_start + 16;
                    if (std.mem.indexOf(u8, text[real_start..], "\\n</USER_REQUEST>")) |req_end| {
                        text = text[real_start .. real_start + req_end];
                    }
                } else if (std.mem.indexOf(u8, text, "<USER_REQUEST>")) |req_start| {
                    const real_start = req_start + 14;
                    if (std.mem.indexOf(u8, text[real_start..], "</USER_REQUEST>")) |req_end| {
                        text = text[real_start .. real_start + req_end];
                    }
                }
                if (!std.mem.startsWith(u8, text, "<system-") and
                    !std.mem.startsWith(u8, text, "<local-"))
                {
                    return try allocator.dupe(u8, truncateUtf8Safe(text, 36));
                }
            }
        }
    }

    return "(新会话)";
}

fn inspectSession(path: []const u8, summary_mode: bool, allocator: std.mem.Allocator) !void {
    const stdout = std.io.getStdOut().writer();
    const file = try std.fs.openFileAbsolute(path, .{});
    defer file.close();

    try stdout.print("\x1b[1;32m=== 会话详情: {s} ===\x1b[0m\n\n", .{path});

    var buf: [64 * 1024]u8 = undefined;
    var line_count: usize = 0;
    var reader = file.reader();

    var first_user: ?[]const u8 = null;
    var last_assistant: ?[]const u8 = null;

    while (reader.readUntilDelimiterOrEof(&buf, '\n') catch null) |line| {
        line_count += 1;
        if (line.len < 10) continue;

        if (std.mem.indexOf(u8, line, "\"role\":\"user\"") != null or
            std.mem.indexOf(u8, line, "\"type\":\"user\"") != null or
            std.mem.indexOf(u8, line, "\"type\":\"USER_INPUT\"") != null)
        {
            var user_text: ?[]const u8 = null;
            if (std.mem.indexOf(u8, line, "\"text\":\"")) |t_idx| {
                const start = t_idx + 8;
                if (std.mem.indexOfScalar(u8, line[start..], '"')) |t_end| {
                    user_text = line[start .. start + t_end];
                }
            } else if (std.mem.indexOf(u8, line, "\"content\":\"")) |c_idx| {
                const start = c_idx + 11;
                var end = start;
                while (end < line.len) : (end += 1) {
                    if (line[end] == '"' and line[end - 1] != '\\') break;
                }
                var t = line[start..end];
                if (std.mem.indexOf(u8, t, "<USER_REQUEST>\\n")) |req_start| {
                    const real_start = req_start + 16;
                    if (std.mem.indexOf(u8, t[real_start..], "\\n</USER_REQUEST>")) |req_end| {
                        t = t[real_start .. real_start + req_end];
                    }
                } else if (std.mem.indexOf(u8, t, "<USER_REQUEST>")) |req_start| {
                    const real_start = req_start + 14;
                    if (std.mem.indexOf(u8, t[real_start..], "</USER_REQUEST>")) |req_end| {
                        t = t[real_start .. real_start + req_end];
                    }
                }
                user_text = t;
            }

            if (user_text) |t| {
                if (!std.mem.startsWith(u8, t, "<system-") and !std.mem.startsWith(u8, t, "<local-")) {
                    if (first_user == null) first_user = try allocator.dupe(u8, t);
                    if (!summary_mode) {
                        try stdout.print("\x1b[1;36m[用户 #{d}]\x1b[0m {s}\n\n", .{ line_count, truncateUtf8Safe(t, 300) });
                    }
                }
            }
        }

        if (std.mem.indexOf(u8, line, "\"role\":\"assistant\"") != null or
            std.mem.indexOf(u8, line, "\"type\":\"assistant\"") != null or
            std.mem.indexOf(u8, line, "\"type\":\"PLANNER_RESPONSE\"") != null)
        {
            var asst_text: ?[]const u8 = null;
            if (std.mem.indexOf(u8, line, "\"text\":\"")) |t_idx| {
                const start = t_idx + 8;
                if (std.mem.indexOfScalar(u8, line[start..], '"')) |t_end| {
                    asst_text = line[start .. start + t_end];
                }
            } else if (std.mem.indexOf(u8, line, "\"content\":\"")) |c_idx| {
                const start = c_idx + 11;
                var end = start;
                while (end < line.len) : (end += 1) {
                    if (line[end] == '"' and line[end - 1] != '\\') break;
                }
                const t = line[start..end];
                if (t.len > 0) {
                    asst_text = t;
                }
            }

            if (asst_text) |t| {
                last_assistant = try allocator.dupe(u8, t);
                if (!summary_mode) {
                    try stdout.print("\x1b[1;35m[助手 #{d}]\x1b[0m {s}\n\n", .{ line_count, truncateUtf8Safe(t, 300) });
                }
            }
        }
    }

    if (summary_mode) {
        try stdout.print("\x1b[1;36m[首条用户需求]\x1b[0m\n{s}\n\n", .{first_user orelse "(无用户输入)"});
        try stdout.print("\x1b[1;32m[最新助手响应/结论]\x1b[0m\n{s}\n", .{last_assistant orelse "(无助手输出)"});
    }
    try stdout.print("\n\x1b[90m--- 共读取 {d} 轮事件 ---\x1b[0m\n", .{line_count});
}

const JsonlScanConfig = struct {
    agent_name: []const u8,
    root_dir: []const u8,
    active_map: ?*const std.StringHashMap(i128) = null,
};

fn scanJsonlDir(
    cfg: JsonlScanConfig,
    list: *std.ArrayList(SessionItem),
    ws_filter: ?[]const u8,
    grep_keywords: []const []const u8,
    show_all: bool,
    allocator: std.mem.Allocator,
) !void {
    var base_dir = std.fs.openDirAbsolute(cfg.root_dir, .{ .iterate = true }) catch return;
    defer base_dir.close();
    var b_iter = base_dir.iterate();
    while (b_iter.next() catch null) |entry| {
        if (entry.kind != .directory) continue;
        var sub_dir = base_dir.openDir(entry.name, .{ .iterate = true }) catch continue;
        defer sub_dir.close();
        var s_iter = sub_dir.iterate();
        while (s_iter.next() catch null) |f_entry| {
            if (f_entry.kind != .file or !std.mem.endsWith(u8, f_entry.name, ".jsonl")) continue;
            const full_path = try std.fmt.allocPrint(allocator, "{s}/{s}/{s}", .{ cfg.root_dir, entry.name, f_entry.name });
            const stat = std.fs.openFileAbsolute(full_path, .{}) catch continue;
            const stat_info = stat.stat() catch {
                stat.close();
                continue;
            };
            stat.close();

            const raw_sid = if (f_entry.name.len > 6) f_entry.name[0 .. f_entry.name.len - 6] else f_entry.name;
            const sid = try allocator.dupe(u8, raw_sid);
            const ws = cleanWorkspace(entry.name);

            if (ws_filter) |w| {
                if (std.mem.indexOf(u8, ws, w) == null) continue;
            }

            if (grep_keywords.len > 0) {
                var all_matched = true;
                for (grep_keywords) |kw| {
                    if (!fileContainsInsensitive(full_path, kw)) {
                        all_matched = false;
                        break;
                    }
                }
                if (!all_matched) continue;
            }

            var title = extractJsonlTitle(full_path, allocator) catch "(空会话)";
            if (!show_all and (std.mem.eql(u8, title, "(空会话)") or std.mem.startsWith(u8, title, "(/"))) {
                continue;
            }

            var eff_mtime = stat_info.mtime;
            if (cfg.active_map) |act_map| {
                var tail_buf: [8192]u8 = undefined;
                if (readFileTail(full_path, &tail_buf)) |tail| {
                    if (act_map.get(sid)) |hb_ns| {
                        if (hb_ns > eff_mtime) eff_mtime = hb_ns;
                        title = try std.fmt.allocPrint(allocator, "⚡ {s}", .{title});
                    } else if (lastLineIsChangeSession(tail)) {
                        if (lastRealTimestampMs(tail)) |ts_ms| {
                            const real_ns: i128 = @as(i128, ts_ms) * std.time.ns_per_ms;
                            if (real_ns < eff_mtime) eff_mtime = real_ns;
                        }
                    }
                }
            }

            var tbuf: [32]u8 = undefined;
            const time_str = formatEpochSeconds(@intCast(@divFloor(eff_mtime, 1_000_000_000)), &tbuf);

            try list.append(.{
                .agent = cfg.agent_name,
                .sid = sid,
                .mtime = eff_mtime,
                .time_str = try allocator.dupe(u8, time_str),
                .ws = ws,
                .size_bytes = stat_info.size,
                .title = title,
                .path = full_path,
            });
        }
    }
}

pub fn main() !void {
    var arena = std.heap.ArenaAllocator.init(std.heap.page_allocator);
    defer arena.deinit();
    const allocator = arena.allocator();

    var args = try std.process.argsWithAllocator(allocator);
    defer args.deinit();
    _ = args.skip();

    var limit: usize = 25;
    var target_agent: AgentType = .all;
    var grep_keywords = std.ArrayList([]const u8).init(allocator);
    var ws_filter: ?[]const u8 = null;
    var show_all: bool = false;
    var do_bench: bool = false;
    var inspect_kw: ?[]const u8 = null;
    var summary_mode: bool = false;

    while (args.next()) |arg| {
        if (std.mem.eql(u8, arg, "-h") or std.mem.eql(u8, arg, "--help")) {
            const out = std.io.getStdOut().writer();
            try out.print(
                \\lane-fast: Lightning-fast multi-agent session scanner (built with Zig)
                \\
                \\Usage:
                \\  lane-fast [-n <N>] [-a <agy|qoder|cb|rea>] [-g <keyword>]... [-w <ws>] [-A]
                \\  lane-fast <SESSION_ID_OR_KEYWORD> [-s]
                \\  lane-fast bench
                \\
                \\Options:
                \\  -n, --limit <N>       Show latest N sessions (default: 25)
                \\  -a, --agent <NAME>    Filter by agent (agy, qoder, codebuddy, reasonix)
                \\  -g, --grep <KEYWORD>  Keyword search across transcripts (repeatable for AND match)
                \\  -w, --workspace <WS>  Filter by workspace name
                \\  -A, --all             Include empty/slash-command sessions
                \\  -s, --summary         Summary mode when inspecting a session
                \\  bench                 Run speed benchmark
                \\
            , .{});
            return;
        } else if (std.mem.eql(u8, arg, "-n") or std.mem.eql(u8, arg, "--limit")) {
            if (args.next()) |num_str| {
                limit = std.fmt.parseInt(usize, num_str, 10) catch 25;
            }
        } else if (std.mem.eql(u8, arg, "-a") or std.mem.eql(u8, arg, "--agent")) {
            if (args.next()) |agent_str| {
                target_agent = AgentType.fromStr(agent_str);
            }
        } else if (std.mem.eql(u8, arg, "-g") or std.mem.eql(u8, arg, "--grep")) {
            if (args.next()) |kw| {
                try grep_keywords.append(kw);
            }
        } else if (std.mem.eql(u8, arg, "-w") or std.mem.eql(u8, arg, "--workspace")) {
            if (args.next()) |ws| {
                ws_filter = ws;
            }
        } else if (std.mem.eql(u8, arg, "-A") or std.mem.eql(u8, arg, "--all")) {
            show_all = true;
        } else if (std.mem.eql(u8, arg, "-s") or std.mem.eql(u8, arg, "--summary")) {
            summary_mode = true;
        } else if (std.mem.eql(u8, arg, "bench")) {
            do_bench = true;
        } else if (!std.mem.startsWith(u8, arg, "-")) {
            inspect_kw = arg;
        } else {
            std.debug.print("未知参数: {s} (使用 -h 查看帮助)\n", .{arg});
            return;
        }
    }

    const start_time = std.time.nanoTimestamp();
    var list = std.ArrayList(SessionItem).init(allocator);

    // 0. 读取 CodeBuddy 活跃进程心跳 (/root/.codebuddy/sessions/*.json)
    //    这是"真正活跃会话"的权威来源：change session 等本地控制指令会把
    //    旧会话 jsonl 的物理 mtime 刷新造成排序倒挂，心跳时间可纠正。
    var active_map = std.StringHashMap(i128).init(allocator);
    {
        var sdir = std.fs.openDirAbsolute("/root/.codebuddy/sessions", .{ .iterate = true }) catch null;
        if (sdir) |*sd| {
            defer sd.close();
            var it = sd.iterate();
            const now_ms: i64 = std.time.milliTimestamp();
            while (it.next() catch null) |e| {
                if (e.kind != .file or !std.mem.endsWith(u8, e.name, ".json")) continue;
                const meta_path = try std.fmt.allocPrint(allocator, "/root/.codebuddy/sessions/{s}", .{e.name});
                const f = std.fs.openFileAbsolute(meta_path, .{}) catch continue;
                var fbuf: [4096]u8 = undefined;
                const n = f.read(&fbuf) catch {
                    f.close();
                    continue;
                };
                f.close();
                const content = fbuf[0..n];

                const sid_key = "\"sessionId\":";
                const si = std.mem.indexOf(u8, content, sid_key) orelse continue;
                const q1 = std.mem.indexOfScalarPos(u8, content, si + sid_key.len, '"') orelse continue;
                const q2 = std.mem.indexOfScalarPos(u8, content, q1 + 1, '"') orelse continue;
                const sess_id = content[q1 + 1 .. q2];

                const cwd_key = "\"cwd\":";
                var ws_dir: []const u8 = "";
                if (std.mem.indexOf(u8, content, cwd_key)) |ci| {
                    const c1 = std.mem.indexOfScalarPos(u8, content, ci + cwd_key.len, '"');
                    if (c1) |cc1| {
                        if (std.mem.indexOfScalarPos(u8, content, cc1 + 1, '"')) |cc2| {
                            // projects 目录名规则：/home/x -> home-x（去首 '/'，'/' 转 '-'）
                            const raw_cwd = std.mem.trim(u8, content[cc1 + 1 .. cc2], "/");
                            const converted = try allocator.dupe(u8, raw_cwd);
                            for (converted) |*ch| {
                                if (ch.* == '/') ch.* = '-';
                            }
                            ws_dir = converted;
                        }
                    }
                }

                const hb_key = "\"lastHeartbeat\":";
                const hi = std.mem.indexOf(u8, content, hb_key) orelse continue;
                // 格式化 JSON 中冒号后有空格，先跳过再取数字
                var p = hi + hb_key.len;
                while (p < content.len and (content[p] == ' ' or content[p] == '\t')) : (p += 1) {}
                var num_end = p;
                while (num_end < content.len and content[num_end] >= '0' and content[num_end] <= '9') : (num_end += 1) {}
                const hb_ms = std.fmt.parseInt(i64, content[p..num_end], 10) catch continue;

                // 心跳 5 分钟内的进程视为活跃
                if (now_ms - hb_ms > 5 * 60 * 1000) continue;

                // change-session 链转移：心跳记录的是进程锚会话，真正的
                // 工作会话在锚会话 jsonl 尾行的 "change session <target>" 里。
                var cur_sid: []const u8 = try allocator.dupe(u8, sess_id);
                var hops: u32 = 0;
                while (hops < 5) : (hops += 1) {
                    const anchor_path = try std.fmt.allocPrint(allocator, "/root/.codebuddy/projects/{s}/{s}.jsonl", .{ ws_dir, cur_sid });
                    var abuf: [8192]u8 = undefined;
                    const anchor_tail = readFileTail(anchor_path, &abuf) orelse break;
                    var tgt_buf: [36]u8 = undefined;
                    const tgt = extractChangeTarget(anchor_tail, &tgt_buf) orelse break;
                    cur_sid = try allocator.dupe(u8, tgt);
                }
                try active_map.put(cur_sid, @as(i128, hb_ms) * std.time.ns_per_ms);
            }
        }
    }

    // 1. 扫描 AGY (结合 conversation_summaries.db 与 conversations/ 实时目录)
    if (target_agent == .all or target_agent == .agy) {
        const SummaryInfo = struct {
            preview: []const u8,
            ws: []const u8,
            steps: i32,
        };
        var summary_map = std.StringHashMap(SummaryInfo).init(allocator);

        var db_ptr: ?*c.sqlite3 = null;
        const rc = c.sqlite3_open_v2("/root/.gemini/antigravity-cli/conversation_summaries.db", &db_ptr, c.SQLITE_OPEN_READONLY, null);
        if (db_ptr) |db| {
            defer _ = c.sqlite3_close(db);
            if (rc == c.SQLITE_OK) {
                var stmt: ?*c.sqlite3_stmt = null;
                const sql = "SELECT conversation_id, preview, step_count, workspace_uris FROM conversation_summaries";
                if (c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == c.SQLITE_OK) {
                    if (stmt) |s| {
                        defer _ = c.sqlite3_finalize(s);
                        while (c.sqlite3_step(s) == c.SQLITE_ROW) {
                            const cid_c = c.sqlite3_column_text(s, 0);
                            const prev_c = c.sqlite3_column_text(s, 1);
                            const steps = c.sqlite3_column_int(s, 2);
                            const ws_c = c.sqlite3_column_text(s, 3);

                            if (cid_c == null) continue;
                            const cid = try allocator.dupe(u8, std.mem.span(cid_c.?));
                            const prev = if (prev_c) |p| try allocator.dupe(u8, std.mem.span(p)) else "";
                            const ws_str = if (ws_c) |w| try allocator.dupe(u8, cleanWorkspace(std.mem.span(w))) else "root";

                            try summary_map.put(cid, .{
                                .preview = prev,
                                .ws = ws_str,
                                .steps = steps,
                            });
                        }
                    }
                }
            }
        }

        // 直接扫描 /root/.gemini/antigravity-cli/conversations/*.db 保证任何正在运行的会话 100% 实时可见
        var conv_dir = std.fs.openDirAbsolute("/root/.gemini/antigravity-cli/conversations", .{ .iterate = true }) catch null;
        if (conv_dir) |*cdir| {
            defer cdir.close();
            var iter = cdir.iterate();
            while (iter.next() catch null) |entry| {
                if (entry.kind == .file and std.mem.endsWith(u8, entry.name, ".db")) {
                    const sid = entry.name[0 .. entry.name.len - 3];
                    const db_file_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/conversations/{s}", .{entry.name});
                    const stat = std.fs.openFileAbsolute(db_file_path, .{}) catch continue;
                    const stat_info = stat.stat() catch {
                        stat.close();
                        continue;
                    };
                    stat.close();

                    var title: []const u8 = "";
                    var ws_str: []const u8 = "root";

                    if (summary_map.get(sid)) |item_sum| {
                        if (!show_all and item_sum.steps == 0 and item_sum.preview.len == 0) continue;
                        title = if (item_sum.preview.len > 0) truncateUtf8Safe(item_sum.preview, 36) else "(空会话)";
                        ws_str = item_sum.ws;
                    } else {
                        // 未在 summary 库中 -> 正在进行中或新生成的会话！
                        const trans_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{sid});
                        const trans_title = extractJsonlTitle(trans_path, allocator) catch null;
                        if (trans_title) |tt| {
                            if (!std.mem.eql(u8, tt, "(空会话)") and !std.mem.eql(u8, tt, "(新会话)")) {
                                title = try std.fmt.allocPrint(allocator, "⚡ (进行中) {s}", .{tt});
                            } else {
                                title = "⚡ (进行中会话)";
                            }
                        } else {
                            title = "⚡ (进行中会话)";
                        }
                    }

                    if (ws_filter) |w| {
                        if (std.mem.indexOf(u8, ws_str, w) == null) continue;
                    }

                    if (grep_keywords.items.len > 0) {
                        var all_matched = true;
                        for (grep_keywords.items) |kw| {
                            const kw_in_title = sliceContainsInsensitive(title, kw);
                            const trans_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{sid});
                            const kw_in_trans = if (!kw_in_title) fileContainsInsensitive(trans_path, kw) else false;
                            const kw_in_db = if (!kw_in_title and !kw_in_trans) blk: {
                                const db_path_z = try allocator.dupeZ(u8, db_file_path);
                                defer allocator.free(db_path_z);
                                break :blk sqliteDbContainsInsensitive(db_path_z, kw);
                            } else false;
                            if (!kw_in_title and !kw_in_trans and !kw_in_db) {
                                all_matched = false;
                                break;
                            }
                        }
                        if (!all_matched) continue;
                    }

                    var time_buf: [32]u8 = undefined;
                    // 统一转换为精确的北京时间（+8），且以真实的物理修改时间为准
                    const time_str = formatEpochSeconds(@intCast(@divFloor(stat_info.mtime, 1_000_000_000)), &time_buf);

                    try list.append(.{
                        .agent = "agy",
                        .sid = try allocator.dupe(u8, sid),
                        .mtime = stat_info.mtime,
                        .time_str = try allocator.dupe(u8, time_str),
                        .ws = try allocator.dupe(u8, ws_str),
                        .size_bytes = stat_info.size,
                        .title = try allocator.dupe(u8, title),
                        .path = db_file_path,
                    });
                }
            }
        }
    }

    // 2. 扫描 Qoder
    if (target_agent == .all or target_agent == .qoder) {
        try scanJsonlDir(.{
            .agent_name = "qoder",
            .root_dir = "/root/.qoder/projects",
        }, &list, ws_filter, grep_keywords.items, show_all, allocator);
    }

    // 3. 扫描 CodeBuddy
    if (target_agent == .all or target_agent == .codebuddy) {
        try scanJsonlDir(.{
            .agent_name = "codebuddy",
            .root_dir = "/root/.codebuddy/projects",
            .active_map = &active_map,
        }, &list, ws_filter, grep_keywords.items, show_all, allocator);
    }

    // 4. 扫描 Reasonix
    if (target_agent == .all or target_agent == .reasonix) {
        var base_dir = std.fs.openDirAbsolute("/root/.reasonix/projects", .{ .iterate = true }) catch null;
        if (base_dir) |*bdir| {
            defer bdir.close();
            var b_iter = bdir.iterate();
            while (b_iter.next() catch null) |entry| {
                if (entry.kind == .directory) {
                    const sess_dir_path = try std.fmt.allocPrint(allocator, "/root/.reasonix/projects/{s}/sessions", .{entry.name});
                    var sdir = std.fs.openDirAbsolute(sess_dir_path, .{ .iterate = true }) catch continue;
                    defer sdir.close();
                    var s_iter = sdir.iterate();
                    while (s_iter.next() catch null) |f_entry| {
                        if (f_entry.kind == .file and std.mem.endsWith(u8, f_entry.name, ".json")) {
                            const full_path = try std.fmt.allocPrint(allocator, "{s}/{s}", .{ sess_dir_path, f_entry.name });
                            const stat = std.fs.openFileAbsolute(full_path, .{}) catch continue;
                            const stat_info = stat.stat() catch {
                                stat.close();
                                continue;
                            };
                            stat.close();

                            const raw_sid = if (f_entry.name.len > 5) f_entry.name[0 .. f_entry.name.len - 5] else f_entry.name;
                            const sid = try allocator.dupe(u8, raw_sid);
                            const ws = cleanWorkspace(entry.name);

                            if (ws_filter) |w| {
                                if (std.mem.indexOf(u8, ws, w) == null) continue;
                            }

                            if (grep_keywords.items.len > 0) {
                                var all_matched = true;
                                for (grep_keywords.items) |kw| {
                                    if (!fileContainsInsensitive(full_path, kw)) {
                                        all_matched = false;
                                        break;
                                    }
                                }
                                if (!all_matched) continue;
                            }

                            var tbuf: [32]u8 = undefined;
                            const time_str = formatEpochSeconds(@intCast(@divFloor(stat_info.mtime, 1_000_000_000)), &tbuf);

                            try list.append(.{
                                .agent = "reasonix",
                                .sid = sid,
                                .mtime = stat_info.mtime,
                                .time_str = try allocator.dupe(u8, time_str),
                                .ws = ws,
                                .size_bytes = stat_info.size,
                                .title = "(Reasonix 会话)",
                                .path = full_path,
                            });
                        }
                    }
                }
            }
        }
    }

    // 检查是否有 inspect 模式
    if (inspect_kw) |kw| {
        for (list.items) |item| {
            if (std.mem.indexOf(u8, item.sid, kw) != null or std.mem.indexOf(u8, item.title, kw) != null) {
                // 先打印该会话在 lane-summary.db 中的结构化里程碑卡片序列
                printSessionCards(item.sid);

                if (std.mem.endsWith(u8, item.path, ".jsonl")) {
                    try inspectSession(item.path, summary_mode, allocator);
                    return;
                } else if (std.mem.endsWith(u8, item.path, ".db")) {
                    // agy 会话：优先找 brain/<sid>/.system_generated/logs/transcript.jsonl
                    const transcript_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{item.sid});
                    if (std.fs.openFileAbsolute(transcript_path, .{})) |file| {
                        file.close();
                        try inspectSession(transcript_path, summary_mode, allocator);
                        return;
                    } else |_| {}

                    // 若 transcript 不存在，则调用 python3 /root/agy-log-viewer.py 兜底完整解析
                    var child_argv = std.ArrayList([]const u8).init(allocator);
                    try child_argv.append("python3");
                    try child_argv.append("/root/agy-log-viewer.py");
                    try child_argv.append(item.path);
                    if (summary_mode) {
                        try child_argv.append("-s");
                    }
                    var child = std.process.Child.init(child_argv.items, allocator);
                    _ = child.spawnAndWait() catch {
                        const stdout = std.io.getStdOut().writer();
                        try stdout.print("会话位于: {s}\n代理: {s}\n标题: {s}\n时间: {s}\n", .{ item.path, item.agent, item.title, item.time_str });
                    };
                    return;
                } else {
                    const stdout = std.io.getStdOut().writer();
                    try stdout.print("会话位于: {s}\n代理: {s}\n标题: {s}\n时间: {s}\n", .{ item.path, item.agent, item.title, item.time_str });
                    return;
                }
            }
        }
        std.debug.print("未找到匹配的会话: {s}\n", .{kw});
        return;
    }

    // 按 mtime 降序排序
    std.mem.sort(SessionItem, list.items, {}, struct {
        fn lessThan(_: void, a: SessionItem, b: SessionItem) bool {
            return a.mtime > b.mtime;
        }
    }.lessThan);

    const elapsed_ns = std.time.nanoTimestamp() - start_time;
    const elapsed_ms = @as(f64, @floatFromInt(elapsed_ns)) / 1_000_000.0;

    // 加载所有会话的最新里程碑摘要卡片 (微秒级)
    const latest_cards = loadLatestLaneCards(allocator);

    const out = std.io.getStdOut().writer();
    const display_count = @min(list.items.len, limit);

    try out.print("\x1b[1;36m{s: <11} {s: <16} {s: <15} {s: <10} {s: <5} {s: <7} {s}\x1b[0m\n", .{ "AGENT", "TIME", "WS", "SID (8位)", "STAT", "SIZE", "SUMMARY / TITLE" });
    try out.print("{s}\n", .{"-" ** 100});

    var sz_buf: [32]u8 = undefined;
    for (list.items[0..display_count]) |item| {
        const short_sid = if (item.sid.len >= 8) item.sid[0..8] else item.sid;
        const sz = SessionItem.formatSize(item.size_bytes, &sz_buf);

        const agent_color: []const u8 = if (std.mem.eql(u8, item.agent, "qoder"))
            "\x1b[35m"
        else if (std.mem.eql(u8, item.agent, "codebuddy"))
            "\x1b[32m"
        else if (std.mem.eql(u8, item.agent, "agy"))
            "\x1b[34m"
        else
            "\x1b[33m";

        const maybe_card = latest_cards.get(item.sid);
        const stat_icon = if (maybe_card) |card| LaneCard.statusIcon(card.status) else "·";

        var display_title: []const u8 = item.title;
        if (maybe_card) |card| {
            if (card.action.len > 0 or card.conclusion.len > 0) {
                const is_active_prefix = std.mem.startsWith(u8, item.title, "⚡ ");
                const prefix = if (is_active_prefix) "⚡ " else "";
                display_title = try std.fmt.allocPrint(allocator, "{s}[{s}] {s}", .{ prefix, card.action, card.conclusion });
            }
        }
        display_title = truncateUtf8Safe(display_title, 42);

        try out.print("{s}[{s: <9}]\x1b[0m \x1b[90m{s: <16}\x1b[0m \x1b[33m{s: <15}\x1b[0m \x1b[36m{s: <10}\x1b[0m {s: <5} \x1b[90m{s: <7}\x1b[0m {s}\n", .{
            agent_color,
            item.agent,
            item.time_str,
            item.ws,
            short_sid,
            stat_icon,
            sz,
            display_title,
        });
    }

    var kw_display_buf: [256]u8 = undefined;
    var kw_display: []const u8 = "";
    if (grep_keywords.items.len == 1) {
        kw_display = grep_keywords.items[0];
    } else if (grep_keywords.items.len > 1) {
        var stream = std.io.fixedBufferStream(&kw_display_buf);
        const writer = stream.writer();
        for (grep_keywords.items, 0..) |k, idx| {
            if (idx > 0) try writer.writeAll(" AND ");
            try writer.writeAll(k);
        }
        kw_display = stream.getWritten();
    }

    try out.print("{s}\n", .{"-" ** 96});
    if (kw_display.len > 0) {
        try out.print("\x1b[32m✔ 找到 {d} 条会话 (显示前 {d} 条) [Grep: \"{s}\"] | 耗时: {d:.2} ms\x1b[0m\n", .{ list.items.len, display_count, kw_display, elapsed_ms });
    } else {
        try out.print("\x1b[32m✔ 找到 {d} 条会话 (显示前 {d} 条) | 耗时: {d:.2} ms\x1b[0m\n", .{ list.items.len, display_count, elapsed_ms });
    }

    if (do_bench) {
        if (kw_display.len > 0) {
            try out.print("\n\x1b[1m[性能基准测试模式]\x1b[0m 深度遍历与 Payload 精确 Grep (\"{s}\") 耗时: \x1b[33m{d:.2} ms\x1b[0m\n", .{ kw_display, elapsed_ms });
        } else {
            try out.print("\n\x1b[1m[性能基准测试模式]\x1b[0m Zig 四 Agent 深度遍历与检索耗时: \x1b[33m{d:.2} ms\x1b[0m\n", .{elapsed_ms});
        }
    }
}

test "sliceContainsInsensitive basic and case matching" {
    const text = "Hello World Zig Language";
    try std.testing.expect(sliceContainsInsensitive(text, "hello"));
    try std.testing.expect(sliceContainsInsensitive(text, "WORLD"));
    try std.testing.expect(sliceContainsInsensitive(text, "Zig"));
    try std.testing.expect(sliceContainsInsensitive(text, "language"));
    try std.testing.expect(!sliceContainsInsensitive(text, "Rust"));
}

test "sliceContainsInsensitive boundary and edge cases" {
    const text = "QuickBrownFox";
    // 单字符
    try std.testing.expect(sliceContainsInsensitive(text, "q"));
    try std.testing.expect(sliceContainsInsensitive(text, "X"));
    try std.testing.expect(!sliceContainsInsensitive(text, "z"));
    // 空 kw
    try std.testing.expect(sliceContainsInsensitive(text, ""));
    // kw >= haystack
    try std.testing.expect(sliceContainsInsensitive(text, "QuickBrownFox"));
    try std.testing.expect(!sliceContainsInsensitive(text, "QuickBrownFoxExtraLong"));
    // 非字母首字符 (数字、下划线、标点)
    const sym_text = "item_123: data_value";
    try std.testing.expect(sliceContainsInsensitive(sym_text, "_123"));
    try std.testing.expect(sliceContainsInsensitive(sym_text, ": data"));
    try std.testing.expect(!sliceContainsInsensitive(sym_text, ": missing"));
}

test "truncateUtf8Safe ascii and multibyte truncation" {
    // 纯 ASCII
    const ascii_text = "HelloWorld";
    try std.testing.expectEqualStrings("Hello", truncateUtf8Safe(ascii_text, 5));
    try std.testing.expectEqualStrings("HelloWorld", truncateUtf8Safe(ascii_text, 20));

    // UTF-8 中文字符（每个汉字 3 字节）
    const zh_text = "你好世界，探索Zig工作流";
    // 截取前 4 个字符 -> "你好世界" (12 字节)
    const truncated_4 = truncateUtf8Safe(zh_text, 4);
    try std.testing.expectEqualStrings("你好世界", truncated_4);
    try std.testing.expectEqual(@as(usize, 12), truncated_4.len);

    // 截取前 5 个字符 -> "你好世界，"
    const truncated_5 = truncateUtf8Safe(zh_text, 5);
    try std.testing.expectEqualStrings("你好世界，", truncated_5);
}

test "lastRealTimestampMs skip control lines and extract timestamp" {
    const fake_tail =
        \\{"id":"msg1","timestamp":1789307000000,"type":"message","text":"normal text 1"}
        \\{"id":"msg2","timestamp":1789307111000,"type":"message","text":"real message"}
        \\{"id":"ctrl1","timestamp":1789307222000,"text":"<system-reminder data-role=\"command-caveat\">Caveat</system-reminder>"}
        \\{"id":"ctrl2","timestamp":1789307333000,"text":"<local-command-stdout>change session 01a09afb-2e3e-7757-a1a2-b667edc79bed</local-command-stdout>"}
        \\
    ;
    const ts = lastRealTimestampMs(fake_tail);
    try std.testing.expect(ts != null);
    // 必须跳过 ctrl1 和 ctrl2，命中 msg2 的 timestamp 1789307111000
    try std.testing.expectEqual(@as(i64, 1789307111000), ts.?);
}

test "LaneCard statusIcon mapping" {
    try std.testing.expectEqualStrings("✅", LaneCard.statusIcon("done"));
    try std.testing.expectEqualStrings("🔄", LaneCard.statusIcon("ongoing"));
    try std.testing.expectEqualStrings("❌", LaneCard.statusIcon("aborted"));
    try std.testing.expectEqualStrings("·", LaneCard.statusIcon("other"));
}
