const std = @import("std");

pub fn extractTraeIntent(path: []const u8, allocator: std.mem.Allocator) ![]const u8 {
    const file = try std.fs.openFileAbsolute(path, .{});
    defer file.close();

    var buf: [65536]u8 = undefined;
    const n = try file.read(&buf);
    const content = buf[0..n];
    const line_end = std.mem.indexOfScalar(u8, content, '\n') orelse content.len;
    const line = std.mem.trim(u8, content[0..line_end], " \t\r\n");
    if (line.len == 0) return try allocator.dupe(u8, "(空会话)");

    var parsed = std.json.parseFromSlice(std.json.Value, allocator, line, .{}) catch
        return try allocator.dupe(u8, "(空会话)");
    defer parsed.deinit();

    if (parsed.value != .object) return try allocator.dupe(u8, "(空会话)");
    if (parsed.value.object.get("intent")) |intent_val| {
        if (intent_val == .string) {
            const intent = std.mem.trim(u8, intent_val.string, " \t\r\n");
            if (intent.len > 0) return try allocator.dupe(u8, truncateUtf8Safe(intent, 40));
        }
    }
    return try allocator.dupe(u8, "(空会话)");
}

pub fn cleanTraeProjectName(raw: []const u8) []const u8 {
    if (std.mem.indexOf(u8, raw, "--p2-")) |idx| return raw[0..idx];
    return raw;
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

pub fn isNoisyTitle(title: []const u8) bool {
    if (title.len == 0) return true;
    if (std.mem.eql(u8, title, "(空会话)")) return true;
    if (std.mem.eql(u8, title, "(新会话)")) return true;
    if (std.mem.startsWith(u8, title, "(/")) return true;
    if (std.mem.indexOf(u8, title, "<command-name>/") != null) return true;
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
