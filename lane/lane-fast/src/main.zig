const std = @import("std");
pub const search = @import("search.zig");

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

pub fn truncateUtf8Safe(text: []const u8, max_chars: usize) []const u8 {
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

/// 默认的单条消息截断上限（字符数）；`--full` 或 `lane msg` 可绕过
pub const MSG_TRUNCATE_CHARS: usize = 300;

/// 统计 UTF-8 字符数（非法字节序列按 1 字符计，与 truncateUtf8Safe 口径一致）
pub fn countUtf8Chars(text: []const u8) usize {
    var count: usize = 0;
    var byte_idx: usize = 0;
    while (byte_idx < text.len) {
        const len = std.unicode.utf8ByteSequenceLength(text[byte_idx]) catch 1;
        if (byte_idx + len > text.len) break;
        byte_idx += len;
        count += 1;
    }
    return count;
}

/// 从一行 JSON 里解析 "step_index":N（兼容冒号两侧空格；缺失或非数字返回 null）
pub fn parseStepIndex(line: []const u8) ?u64 {
    const key = "\"step_index\"";
    const key_idx = std.mem.indexOf(u8, line, key) orelse return null;
    var i = key_idx + key.len;
    while (i < line.len and (line[i] == ' ' or line[i] == '\t')) : (i += 1) {}
    if (i >= line.len or line[i] != ':') return null;
    i += 1;
    while (i < line.len and (line[i] == ' ' or line[i] == '\t')) : (i += 1) {}
    var value: u64 = 0;
    var digits: usize = 0;
    while (i < line.len and line[i] >= '0' and line[i] <= '9') : (i += 1) {
        value = value * 10 + @as(u64, line[i] - '0');
        digits += 1;
    }
    if (digits == 0) return null;
    return value;
}

/// 打印消息序号标签：优先用底层 step_index（与 `lane msg` 取值口径一致），物理行号以 (line:N) 附注
fn printMessageTag(writer: anytype, label: []const u8, style: []const u8, line_no: usize, step: ?u64) !void {
    if (step) |s| {
        try writer.print("\x1b[{s}m[{s} #{d}]\x1b[0m\x1b[90m(line:{d})\x1b[0m ", .{ style, label, s, line_no });
    } else {
        try writer.print("\x1b[{s}m[{s} #{d}]\x1b[0m ", .{ style, label, line_no });
    }
}

/// 打印消息正文：超限时截断并显式标注（全文 X 字 / 已显示前 N 字）；full_mode 时原样输出
fn printMessageBody(writer: anytype, text: []const u8, full_mode: bool) !void {
    if (full_mode) {
        try writer.print("{s}", .{text});
        return;
    }
    const total = countUtf8Chars(text);
    if (total <= MSG_TRUNCATE_CHARS) {
        try writer.print("{s}", .{text});
        return;
    }
    try writer.print("{s}\x1b[33m...[已截断：全文 {d} 字，此处仅显示前 {d} 字；用 `lane msg <会话ID> <序号>` 或加 --full 查看全文]\x1b[0m", .{ truncateUtf8Safe(text, MSG_TRUNCATE_CHARS), total, MSG_TRUNCATE_CHARS });
}

/// 解析位于 text[i..] 的 `\uXXXX` 转义（i 指向 'u'），成功返回码元并置 consumed=6；失败返回 null
fn parseUnicodeEscapeUnit(text: []const u8, i: usize, out: *u16) bool {
    if (i + 5 >= text.len or text[i] != 'u') return false;
    var v: u16 = 0;
    for (text[i + 1 .. i + 5]) |h| {
        const d: u16 = switch (h) {
            '0'...'9' => h - '0',
            'a'...'f' => h - 'a' + 10,
            'A'...'F' => h - 'A' + 10,
            else => return false,
        };
        v = v * 16 + d;
    }
    out.* = v;
    return true;
}

/// 将 jsonl 里切片出的 JSON 转义文本还原为可读文本并流式写入 writer：
/// \n \t \r \b \f \" \\ \/ 与 \uXXXX（含代理对）→ 实际字符；非法转义原样保留
fn writeJsonUnescaped(writer: anytype, text: []const u8) !void {
    var i: usize = 0;
    while (i < text.len) {
        const ch = text[i];
        if (ch != '\\') {
            try writer.writeByte(ch);
            i += 1;
            continue;
        }
        if (i + 1 >= text.len) {
            try writer.writeByte(ch);
            break;
        }
        switch (text[i + 1]) {
            'n' => try writer.writeByte('\n'),
            't' => try writer.writeByte('\t'),
            'r' => try writer.writeByte('\r'),
            'b' => try writer.writeByte(0x08),
            'f' => try writer.writeByte(0x0C),
            '"' => try writer.writeByte('"'),
            '\\' => try writer.writeByte('\\'),
            '/' => try writer.writeByte('/'),
            'u' => {
                var unit: u16 = 0;
                if (!parseUnicodeEscapeUnit(text, i + 1, &unit)) {
                    try writer.writeByte(ch);
                    i += 1;
                    continue;
                }
                var cp: u21 = unit;
                var consumed: usize = 6; // \uXXXX
                if (unit >= 0xD800 and unit <= 0xDBFF) {
                    // 高代理：尝试合并紧随的低代理
                    var lo: u16 = 0;
                    if (i + 7 < text.len and text[i + 6] == '\\' and
                        parseUnicodeEscapeUnit(text, i + 7, &lo) and
                        lo >= 0xDC00 and lo <= 0xDFFF)
                    {
                        cp = 0x10000 + ((@as(u21, unit) - 0xD800) << 10) + (@as(u21, lo) - 0xDC00);
                        consumed = 12;
                    }
                }
                var utf8_buf: [4]u8 = undefined;
                const n = std.unicode.utf8Encode(cp, &utf8_buf) catch {
                    // 非法码点，原样输出
                    try writer.writeAll(text[i .. i + consumed]);
                    i += consumed;
                    continue;
                };
                try writer.writeAll(utf8_buf[0..n]);
                i += consumed;
                continue;
            },
            else => {
                try writer.writeByte(ch);
                i += 1;
                continue;
            },
        }
        i += 2;
    }
}

const LineMsg = struct {
    user_text: ?[]const u8 = null,
    assistant_text: ?[]const u8 = null,
};

/// 提取一行内的消息正文（优先 "text" 字段，其次 "content"；content 分支会剥离 <USER_REQUEST> 包裹）
fn extractMsgText(line: []const u8) ?[]const u8 {
    if (std.mem.indexOf(u8, line, "\"text\":\"")) |t_idx| {
        const start = t_idx + 8;
        if (std.mem.indexOfScalar(u8, line[start..], '"')) |t_end| {
            return line[start .. start + t_end];
        }
        return null;
    }
    if (std.mem.indexOf(u8, line, "\"content\":\"")) |c_idx| {
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
        return t;
    }
    return null;
}

/// 解析一行 jsonl 里的用户/助手消息（过滤 <system-*/<local-* 控制行；空助手文本不产出）
fn parseLineMsg(line: []const u8) LineMsg {
    var out = LineMsg{};

    const is_user = std.mem.indexOf(u8, line, "\"role\":\"user\"") != null or
        std.mem.indexOf(u8, line, "\"type\":\"user\"") != null or
        std.mem.indexOf(u8, line, "\"type\":\"USER_INPUT\"") != null;
    if (is_user) {
        if (extractMsgText(line)) |t| {
            if (!std.mem.startsWith(u8, t, "<system-") and !std.mem.startsWith(u8, t, "<local-")) {
                out.user_text = t;
            }
        }
    }

    const is_assistant = std.mem.indexOf(u8, line, "\"role\":\"assistant\"") != null or
        std.mem.indexOf(u8, line, "\"type\":\"assistant\"") != null or
        std.mem.indexOf(u8, line, "\"type\":\"PLANNER_RESPONSE\"") != null;
    if (is_assistant) {
        if (extractMsgText(line)) |t| {
            if (t.len > 0) out.assistant_text = t;
        }
    }

    return out;
}

/// 判断 seq 是否作为 step_index 出现在文件里（供 `lane msg` 在 step_index 与物理行号之间自动选口径）
fn findStepIndex(path: []const u8, seq: u64) bool {
    const file = std.fs.openFileAbsolute(path, .{}) catch return false;
    defer file.close();
    var buf: [64 * 1024]u8 = undefined;
    var reader = file.reader();
    while (reader.readUntilDelimiterOrEof(&buf, '\n') catch null) |line| {
        if (parseStepIndex(line)) |s| {
            if (s == seq) return true;
        }
    }
    return false;
}

/// `lane msg <会话ID> <序号> [--line]`：输出单条消息全文（永不截断），返回是否命中
fn printSingleMessage(path: []const u8, seq: u64, force_by_line: bool) !bool {
    const stdout = std.io.getStdOut().writer();
    // 口径选择：显式 --line 用物理行号；否则 seq 命中 step_index 就用 step_index，否则回退行号
    const by_line = force_by_line or !findStepIndex(path, seq);

    const file = try std.fs.openFileAbsolute(path, .{});
    defer file.close();

    try stdout.print("\x1b[1;32m=== 单条消息全文: {s} ===\x1b[0m\n\x1b[90m匹配口径: {s} {d}\x1b[0m\n\n", .{ path, if (by_line) "物理行号" else "step_index", seq });

    var buf: [64 * 1024]u8 = undefined;
    var line_count: usize = 0;
    var reader = file.reader();

    while (true) {
        const maybe_line = reader.readUntilDelimiterOrEof(&buf, '\n') catch |err| {
            try stdout.print("\x1b[31m[!] 读取第 {d} 行失败（{s}），已停止解析。\x1b[0m\n", .{ line_count + 1, @errorName(err) });
            break;
        };
        const line = maybe_line orelse break;
        line_count += 1;

        const step = parseStepIndex(line);
        const hit = if (by_line) (line_count == seq) else (if (step) |s| s == seq else false);
        if (!hit) continue;

        const msg = parseLineMsg(line);
        var printed: usize = 0;
        if (msg.user_text) |t| {
            try printMessageTag(stdout, "用户", "1;36", line_count, step);
            try writeJsonUnescaped(stdout, t);
            try stdout.print("\n\n", .{});
            printed += 1;
        }
        if (msg.assistant_text) |t| {
            try printMessageTag(stdout, "助手", "1;35", line_count, step);
            try writeJsonUnescaped(stdout, t);
            try stdout.print("\n\n", .{});
            printed += 1;
        }
        if (printed == 0) {
            // 兜底：该条无 text/content（如纯 tool_calls / thinking 记录），直接给未截断的原始 JSON，保证不空手而归
            try stdout.print("\x1b[33m[原始 JSON] 该条无 text/content 字段，以下为未截断的原始记录：\x1b[0m\n{s}\n\n", .{line});
        }
        try stdout.print("\x1b[90m--- 命中: 文件第 {d} 行", .{line_count});
        if (step) |s| try stdout.print("（step_index {d}）", .{s});
        try stdout.print(" ---\x1b[0m\n", .{});
        return true;
    }
    return false;
}

pub fn formatEpochSeconds(epoch_sec: i64, buf: []u8) []const u8 {
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

pub fn cleanWorkspace(raw: []const u8) []const u8 {
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
pub fn readFileTail(path: []const u8, buf: []u8) ?[]const u8 {
    const f = std.fs.openFileAbsolute(path, .{}) catch return null;
    defer f.close();
    const size = f.getEndPos() catch return null;
    const start = if (size > buf.len) size - buf.len else 0;
    f.seekTo(start) catch return null;
    const n = f.read(buf) catch return null;
    return buf[0..n];
}

/// 文件最后一条消息是否为 change session 切换控制行
pub fn lastLineIsChangeSession(tail: []const u8) bool {
    const trimmed = std.mem.trimRight(u8, tail, " \t\r\n");
    if (trimmed.len == 0) return false;
    const line_start = std.mem.lastIndexOfScalar(u8, trimmed, '\n') orelse 0;
    const line = trimmed[line_start..];
    return std.mem.indexOf(u8, line, "<local-command-stdout>") != null and
        std.mem.indexOf(u8, line, "change session ") != null;
}

/// 从尾部提取最后一个 change session 的目标会话 UUID（36 字符）
pub fn extractChangeTarget(tail: []const u8, out: []u8) ?[]const u8 {
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
pub fn lastRealTimestampMs(tail: []const u8) ?i64 {
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

pub fn extractJsonlTitle(path: []const u8, allocator: std.mem.Allocator) ![]const u8 {
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

fn inspectSession(path: []const u8, summary_mode: bool, full_mode: bool, allocator: std.mem.Allocator) !void {
    const stdout = std.io.getStdOut().writer();
    const file = try std.fs.openFileAbsolute(path, .{});
    defer file.close();

    try stdout.print("\x1b[1;32m=== 会话详情: {s} ===\x1b[0m\n\n", .{path});

    var buf: [64 * 1024]u8 = undefined;
    var line_count: usize = 0;
    var reader = file.reader();

    var first_user: ?[]const u8 = null;
    var last_assistant: ?[]const u8 = null;

    while (true) {
        const maybe_line = reader.readUntilDelimiterOrEof(&buf, '\n') catch |err| {
            // 旧版把 StreamTooLong 静默吞成 null 直接退出，导致超长行（>64KB）时 view 静默中断；这里显式告警
            try stdout.print("\x1b[31m[!] 读取第 {d} 行失败（{s}），可能是单行超过 64KB 缓冲，已停止解析。\x1b[0m\n", .{ line_count + 1, @errorName(err) });
            break;
        };
        const line = maybe_line orelse break;
        line_count += 1;
        if (line.len < 10) continue;

        const msg = parseLineMsg(line);
        const step = parseStepIndex(line);

        if (msg.user_text) |t| {
            if (first_user == null) first_user = try allocator.dupe(u8, t);
            if (!summary_mode) {
                try printMessageTag(stdout, "用户", "1;36", line_count, step);
                try printMessageBody(stdout, t, full_mode);
                try stdout.print("\n\n", .{});
            }
        }

        if (msg.assistant_text) |t| {
            last_assistant = try allocator.dupe(u8, t);
            if (!summary_mode) {
                try printMessageTag(stdout, "助手", "1;35", line_count, step);
                try printMessageBody(stdout, t, full_mode);
                try stdout.print("\n\n", .{});
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
            const ws = try allocator.dupe(u8, cleanWorkspace(entry.name));

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

fn renderSessionList(
    items: []const SessionItem,
    limit: usize,
    kw_display: []const u8,
    elapsed_ms: f64,
    do_bench: bool,
    is_search: bool,
    allocator: std.mem.Allocator,
) !void {
    const latest_cards = loadLatestLaneCards(allocator);
    const out = std.io.getStdOut().writer();
    const display_count = @min(items.len, limit);

    try out.print("\x1b[1;36m{s: <11} {s: <16} {s: <15} {s: <10} {s: <5} {s: <7} {s}\x1b[0m\n", .{ "AGENT", "TIME", "WS", "SID (8位)", "STAT", "SIZE", "SUMMARY / TITLE" });
    try out.print("{s}\n", .{"-" ** 100});

    var sz_buf: [32]u8 = undefined;
    for (items[0..display_count]) |item| {
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

    try out.print("{s}\n", .{"-" ** 96});
    const tag_label = if (is_search) "Search" else "Grep";
    if (kw_display.len > 0) {
        try out.print("\x1b[32m✔ 找到 {d} 条会话 (显示前 {d} 条) [{s}: \"{s}\"] | 耗时: {d:.2} ms\x1b[0m\n", .{ items.len, display_count, tag_label, kw_display, elapsed_ms });
    } else {
        try out.print("\x1b[32m✔ 找到 {d} 条会话 (显示前 {d} 条) | 耗时: {d:.2} ms\x1b[0m\n", .{ items.len, display_count, elapsed_ms });
    }

    if (do_bench) {
        if (kw_display.len > 0) {
            if (is_search) {
                try out.print("\n\x1b[1m[性能基准测试模式]\x1b[0m 极速倒排索引检索 (\"{s}\") 耗时: \x1b[33m{d:.2} ms\x1b[0m\n", .{ kw_display, elapsed_ms });
            } else {
                try out.print("\n\x1b[1m[性能基准测试模式]\x1b[0m 深度遍历与 Payload 精确 Grep (\"{s}\") 耗时: \x1b[33m{d:.2} ms\x1b[0m\n", .{ kw_display, elapsed_ms });
            }
        } else {
            try out.print("\n\x1b[1m[性能基准测试模式]\x1b[0m Zig 四 Agent 深度遍历与检索耗时: \x1b[33m{d:.2} ms\x1b[0m\n", .{elapsed_ms});
        }
    }
}

fn runIndexSearch(
    keywords: []const []const u8,
    limit: usize,
    ws_filter: ?[]const u8,
    target_agent: AgentType,
    do_bench: bool,
    start_time: i128,
    is_search_cmd: bool,
    allocator: std.mem.Allocator,
) !bool {
    const raw_results = (search.searchWithIndex(keywords, allocator) catch return false) orelse return false;

    var filtered = std.ArrayList(SessionItem).init(allocator);
    for (raw_results.items) |item| {
        if (ws_filter) |w| {
            if (std.mem.indexOf(u8, item.ws, w) == null) continue;
        }
        if (target_agent != .all) {
            const ag = AgentType.fromStr(item.agent);
            if (ag != target_agent) continue;
        }
        try filtered.append(item);
    }

    std.mem.sort(SessionItem, filtered.items, {}, struct {
        fn lessThan(_: void, a: SessionItem, b: SessionItem) bool {
            return a.mtime > b.mtime;
        }
    }.lessThan);

    const elapsed_ns = std.time.nanoTimestamp() - start_time;
    const elapsed_ms = @as(f64, @floatFromInt(elapsed_ns)) / 1_000_000.0;

    var kw_display_buf: [256]u8 = undefined;
    var kw_display: []const u8 = "";
    if (keywords.len == 1) {
        kw_display = keywords[0];
    } else if (keywords.len > 1) {
        var stream = std.io.fixedBufferStream(&kw_display_buf);
        const writer = stream.writer();
        for (keywords, 0..) |k, idx| {
            if (idx > 0) try writer.writeAll(" AND ");
            try writer.writeAll(k);
        }
        kw_display = stream.getWritten();
    }

    try renderSessionList(filtered.items, limit, kw_display, elapsed_ms, do_bench, is_search_cmd, allocator);
    return true;
}

pub fn main() !void {
    const start_time = std.time.nanoTimestamp();
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
    var is_index_subcmd: bool = false;
    var is_search_subcmd: bool = false;
    var force_index: bool = false;
    var is_msg_subcmd: bool = false;
    var msg_sid: ?[]const u8 = null;
    var msg_seq: ?u64 = null;
    var msg_by_line: bool = false;
    var full_mode: bool = false;

    while (args.next()) |arg| {
        if (std.mem.eql(u8, arg, "-h") or std.mem.eql(u8, arg, "--help")) {
            const stdout = std.io.getStdOut().writer();
            try stdout.print(
                \\Usage:
                \\  lane-fast [-n <N>] [-a <agy|qoder|cb|rea>] [-g <keyword>]... [-w <ws>] [-A]
                \\  lane-fast search <keyword>... [-n <N>] [-w <ws>] [-a <agent>] [--bench]
                \\  lane-fast index [--force]
                \\  lane-fast <SESSION_ID_OR_KEYWORD> [-s] [--full]
                \\  lane-fast msg <SESSION_ID> <SEQ> [--line]
                \\  lane-fast bench
                \\
                \\Commands:
                \\  search <keyword>...   Fast full-text search across transcripts using inverted index (< 5ms)
                \\  index                 Build or incrementally update inverted index (/root/.agent/lane-search.idx)
                \\  msg <SID> <SEQ>       Print ONE message in FULL (never truncated). SEQ matches step_index; --line matches physical line number
                \\  bench                 Run speed benchmark across all search strategies
                \\
                \\Options:
                \\  -n, --limit <N>       Show latest N sessions (default: 25)
                \\  -a, --agent <NAME>    Filter by agent (agy, qoder, codebuddy, reasonix)
                \\  -g, --grep <KEYWORD>  Keyword search across transcripts (indexed with fallback to grep)
                \\  -w, --workspace <WS>  Filter by workspace name
                \\  -A, --all             Include empty/slash-command sessions
                \\  -s, --summary         Summary mode when inspecting a session
                \\      --full            Print message bodies in full (disable the 300-char truncation)
                \\      --line            (msg) Match SEQ against the physical line number instead of step_index
                \\  --force, -f           Force full rebuild of index (used with 'index')
                \\  --bench               Print precise elapsed search time
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
        } else if (std.mem.eql(u8, arg, "index")) {
            is_index_subcmd = true;
        } else if (std.mem.eql(u8, arg, "search")) {
            is_search_subcmd = true;
        } else if (std.mem.eql(u8, arg, "--force") or std.mem.eql(u8, arg, "-f")) {
            force_index = true;
        } else if (std.mem.eql(u8, arg, "--bench")) {
            do_bench = true;
        } else if (std.mem.eql(u8, arg, "--full")) {
            full_mode = true;
        } else if (std.mem.eql(u8, arg, "--line")) {
            msg_by_line = true;
        } else if (std.mem.eql(u8, arg, "msg")) {
            is_msg_subcmd = true;
        } else if (!std.mem.startsWith(u8, arg, "-")) {
            if (is_msg_subcmd) {
                if (msg_sid == null) {
                    msg_sid = arg;
                } else if (msg_seq == null) {
                    msg_seq = std.fmt.parseInt(u64, arg, 10) catch null;
                    if (msg_seq == null) {
                        std.debug.print("序号必须是数字: {s}\n", .{arg});
                        std.process.exit(1);
                    }
                } else {
                    std.debug.print("msg 参数过多: {s}（用法: lane msg <会话ID> <序号> [--line]）\n", .{arg});
                    std.process.exit(1);
                }
            } else if (is_search_subcmd) {
                try grep_keywords.append(arg);
            } else {
                inspect_kw = arg;
            }
        } else {
            std.debug.print("未知参数: {s} (使用 -h 查看帮助)\n", .{arg});
            return;
        }
    }

    if (is_msg_subcmd and (msg_sid == null or msg_seq == null)) {
        std.debug.print("用法: lane msg <会话ID> <序号> [--line]\n  序号默认按 step_index 匹配；--line 则按物理行号匹配\n", .{});
        std.process.exit(1);
    }

    if (is_index_subcmd) {
        try search.buildOrUpdateIndex(force_index, allocator);
        return;
    }

    if (is_search_subcmd or grep_keywords.items.len > 0) {
        if (grep_keywords.items.len > 0 and inspect_kw == null) {
            const ok = runIndexSearch(grep_keywords.items, limit, ws_filter, target_agent, do_bench, start_time, is_search_subcmd, allocator) catch false;
            if (ok) return;
        }
    }

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

    // `lane msg <会话ID> <序号>`：输出单条消息全文（不截断）
    if (is_msg_subcmd) {
        const sid_kw = msg_sid.?;
        var matched: ?SessionItem = null;
        for (list.items) |item| {
            if (std.mem.indexOf(u8, item.sid, sid_kw) != null) {
                matched = item;
                break;
            }
        }
        if (matched == null) {
            std.debug.print("未找到会话: {s}（可先用 `lane -g <关键词>` 或 `lane -a <agent>` 查 SID）\n", .{sid_kw});
            std.process.exit(1);
        }
        const item = matched.?;
        // 解析真实转录文件：agy 走 brain/<sid>/transcript.jsonl，其余用扫描到的 path
        var transcript_path: []const u8 = item.path;
        if (std.mem.endsWith(u8, item.path, ".db")) {
            const candidate = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{item.sid});
            if (std.fs.openFileAbsolute(candidate, .{})) |f| {
                f.close();
                transcript_path = candidate;
            } else |_| {}
        }
        const hit = printSingleMessage(transcript_path, msg_seq.?, msg_by_line) catch false;
        if (!hit) {
            std.debug.print("未找到序号 {d}（已自动尝试 step_index 与物理行号）；先用 `lane view {s}` 查看已列出的序号\n", .{ msg_seq.?, item.sid });
            std.process.exit(1);
        }
        return;
    }

    // 检查是否有 inspect 模式
    if (inspect_kw) |kw| {
        for (list.items) |item| {
            if (std.mem.indexOf(u8, item.sid, kw) != null or std.mem.indexOf(u8, item.title, kw) != null) {
                // 先打印该会话在 lane-summary.db 中的结构化里程碑卡片序列
                printSessionCards(item.sid);

                if (std.mem.endsWith(u8, item.path, ".jsonl")) {
                    try inspectSession(item.path, summary_mode, full_mode, allocator);
                    return;
                } else if (std.mem.endsWith(u8, item.path, ".db")) {
                    // agy 会话：优先找 brain/<sid>/.system_generated/logs/transcript.jsonl
                    const transcript_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{item.sid});
                    if (std.fs.openFileAbsolute(transcript_path, .{})) |file| {
                        file.close();
                        try inspectSession(transcript_path, summary_mode, full_mode, allocator);
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

    if (grep_keywords.items.len > 0 and list.items.len > 0) {
        // 传统 fallback 命中，标记索引 dirty，为后续增量重建提供信号
        search.markIndexDirty();
    }

    try renderSessionList(list.items, limit, kw_display, elapsed_ms, do_bench, false, allocator);
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

test "parseStepIndex reads step_index and rejects malformed" {
    try std.testing.expectEqual(@as(?u64, 3970), parseStepIndex("{\"step_index\":3970,\"source\":\"MODEL\"}"));
    try std.testing.expectEqual(@as(?u64, 27), parseStepIndex("{\"step_index\": 27, \"type\":\"x\"}"));
    try std.testing.expectEqual(@as(?u64, 5), parseStepIndex("{\"step_index\" : 5}"));
    try std.testing.expectEqual(@as(?u64, 0), parseStepIndex("{\"step_index\":0}"));
    try std.testing.expectEqual(@as(?u64, null), parseStepIndex("{\"type\":\"GENERIC\"}"));
    try std.testing.expectEqual(@as(?u64, null), parseStepIndex("{\"step_index\":\"abc\"}"));
    try std.testing.expectEqual(@as(?u64, null), parseStepIndex("{\"step_index\":}"));
    try std.testing.expectEqual(@as(?u64, null), parseStepIndex("{\"step_index\"}"));
}

test "countUtf8Chars counts chars not bytes" {
    try std.testing.expectEqual(@as(usize, 0), countUtf8Chars(""));
    try std.testing.expectEqual(@as(usize, 5), countUtf8Chars("Hello"));
    try std.testing.expectEqual(@as(usize, 6), countUtf8Chars("你好世界，!"));
    const long = "一二三四五六七八九十" ** 5;
    try std.testing.expectEqual(@as(usize, 50), countUtf8Chars(long));
    try std.testing.expectEqual(@as(usize, 4), countUtf8Chars(truncateUtf8Safe(long, 4)));
}

test "printMessageBody marks truncation with full length" {
    var short_buf: [128]u8 = undefined;
    var short_stream = std.io.fixedBufferStream(&short_buf);
    try printMessageBody(short_stream.writer(), "短消息", false);
    try std.testing.expectEqualStrings("短消息", short_stream.getWritten());

    var long_buf: [4096]u8 = undefined;
    var long_stream = std.io.fixedBufferStream(&long_buf);
    const long = "x" ** 400;
    try printMessageBody(long_stream.writer(), long, false);
    const got = long_stream.getWritten();
    // 必须带显式截断标记 + 全文字数，且正文只给前 300 字
    try std.testing.expect(std.mem.indexOf(u8, got, "已截断") != null);
    try std.testing.expect(std.mem.indexOf(u8, got, "全文 400 字") != null);
    try std.testing.expect(std.mem.startsWith(u8, got, "x" ** 300));

    var full_buf: [4096]u8 = undefined;
    var full_stream = std.io.fixedBufferStream(&full_buf);
    try printMessageBody(full_stream.writer(), long, true);
    try std.testing.expectEqualStrings(long, full_stream.getWritten());
}

test "writeJsonUnescaped restores escapes incl surrogate pairs" {
    var buf: [1024]u8 = undefined;
    var stream = std.io.fixedBufferStream(&buf);
    const w = stream.writer();

    try writeJsonUnescaped(w, "事实1：13GB\\n耗时约 22 秒\\t结束");
    try std.testing.expectEqualStrings("事实1：13GB\n耗时约 22 秒\t结束", stream.getWritten());

    stream.reset();
    try writeJsonUnescaped(w, "引号\\\"与反斜杠\\\\与斜杠\\/与\\r回车");
    try std.testing.expectEqualStrings("引号\"与反斜杠\\与斜杠/与\r回车", stream.getWritten());

    stream.reset();
    // \uXXXX（BMP）与代理对 emoji，以及非法转义原样保留
    try writeJsonUnescaped(w, "\\u4f60\\u597d \\uD83D\\uDE00 \\x \\uZZZZ 尾部\\");
    try std.testing.expectEqualStrings("你好 😀 \\x \\uZZZZ 尾部\\", stream.getWritten());
}

test "parseLineMsg extracts user (USER_REQUEST unwrap), assistant, filters control lines" {
    const user_line = "{\"type\":\"USER_INPUT\",\"content\":\"<USER_REQUEST>\\n你好 lane\\n</USER_REQUEST>\"}";
    const u = parseLineMsg(user_line);
    try std.testing.expect(u.user_text != null);
    try std.testing.expectEqualStrings("你好 lane", u.user_text.?);

    const sys_line = "{\"type\":\"USER_INPUT\",\"content\":\"<system-reminder>ctrl</system-reminder>\"}";
    try std.testing.expect(parseLineMsg(sys_line).user_text == null);

    const asst_line = "{\"step_index\":12,\"type\":\"PLANNER_RESPONSE\",\"content\":\"结论：可用\"}";
    const a = parseLineMsg(asst_line);
    try std.testing.expect(a.assistant_text != null);
    try std.testing.expectEqualStrings("结论：可用", a.assistant_text.?);

    const text_field_line = "{\"role\":\"assistant\",\"text\":\"from text field\"}";
    try std.testing.expectEqualStrings("from text field", parseLineMsg(text_field_line).assistant_text.?);

    const tool_only = "{\"step_index\":13,\"type\":\"PLANNER_RESPONSE\",\"tool_calls\":[]}";
    const t = parseLineMsg(tool_only);
    try std.testing.expect(t.assistant_text == null);
    try std.testing.expect(t.user_text == null);
}

test "findStepIndex scans a real jsonl file" {
    const path = "/tmp/lane-fast-findstep-test.jsonl";
    {
        const f = try std.fs.createFileAbsolute(path, .{});
        defer f.close();
        try f.writeAll("{\"step_index\":10,\"type\":\"GENERIC\",\"content\":\"a\"}\n");
        try f.writeAll("{\"step_index\":11,\"type\":\"GENERIC\",\"content\":\"b\"}\n");
    }
    defer std.fs.deleteFileAbsolute(path) catch {};
    try std.testing.expect(findStepIndex(path, 10));
    try std.testing.expect(findStepIndex(path, 11));
    try std.testing.expect(!findStepIndex(path, 12));
    try std.testing.expect(!findStepIndex("/tmp/definitely-missing-lane-fast.jsonl", 1));
}

test {
    std.testing.refAllDecls(search);
}
