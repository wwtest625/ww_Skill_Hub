const std = @import("std");
const main = @import("main.zig");

pub const INDEX_PATH = "/root/.agent/lane-search.idx";
pub const MAGIC: [8]u8 = "LSEIDX01".*;
pub const CURRENT_VERSION: u32 = 1;

pub const IndexHeader = extern struct {
    magic: [8]u8,
    version: u32,
    num_docs: u32,
    num_terms: u32,
    created_at_ms: i64,
    docs_idx_offset: u64,
    docs_data_offset: u64,
    terms_idx_offset: u64,
    terms_data_offset: u64,
    postings_offset: u64,
    checksum: u32,
};

pub const DocMetadata = struct {
    agent: []const u8,
    sid: []const u8,
    mtime: i128,
    time_str: []const u8,
    ws: []const u8,
    size_bytes: u64,
    title: []const u8,
    path: []const u8,
};

pub fn encodeVarint(val: u32, buf: []u8) usize {
    var v = val;
    var i: usize = 0;
    while (v >= 0x80) {
        buf[i] = @as(u8, @intCast(v & 0x7F)) | 0x80;
        v >>= 7;
        i += 1;
    }
    buf[i] = @as(u8, @intCast(v & 0x7F));
    return i + 1;
}

pub fn decodeVarint(bytes: []const u8, idx: *usize) ?u32 {
    var result: u32 = 0;
    var shift: u5 = 0;
    while (idx.* < bytes.len) {
        const b = bytes[idx.*];
        idx.* += 1;
        result |= @as(u32, b & 0x7F) << shift;
        if ((b & 0x80) == 0) return result;
        shift += 7;
        if (shift >= 35) return null;
    }
    return null;
}

pub fn computeAdler32(data: []const u8) u32 {
    var a: u32 = 1;
    var b: u32 = 0;
    const MOD_ADLER: u32 = 65521;
    for (data) |byte| {
        a = (a + byte) % MOD_ADLER;
        b = (b + a) % MOD_ADLER;
    }
    return (b << 16) | a;
}

pub inline fn isAsciiWordChar(c_in: u8) bool {
    return (c_in >= 'a' and c_in <= 'z') or
        (c_in >= 'A' and c_in <= 'Z') or
        (c_in >= '0' and c_in <= '9') or
        (c_in == '_');
}

/// 提取去重 tokens:
/// 1. 处理 JSON 转义符 (\n, \r, \t, \\, \")
/// 2. ASCII 单词小写化，并在单词内部滑动提取 3-gram 和 4-gram
/// 3. 中文相邻两字 bigram 及单字
pub fn tokenizeText(text: []const u8, token_map: *std.StringHashMap(void), allocator: std.mem.Allocator) !void {
    if (text.len == 0) return;

    var i: usize = 0;
    var last_cjk: ?[3]u8 = null;
    var stack_buf: [64]u8 = undefined;

    while (i < text.len) {
        // 处理 JSON 转义符 (\n, \r, \t, \\, \")
        if (text[i] == '\\' and i + 1 < text.len) {
            const next = text[i + 1];
            if (next == 'n' or next == 'r' or next == 't' or next == '\\' or next == '"') {
                i += 2;
                last_cjk = null;
                continue;
            }
        }

        const b = text[i];

        // 1. ASCII 词提取
        if (isAsciiWordChar(b)) {
            last_cjk = null;
            const start = i;
            while (i < text.len and isAsciiWordChar(text[i])) : (i += 1) {}
            const word_raw = text[start..i];

            if (word_raw.len <= 64) {
                for (word_raw, 0..) |ch, k| {
                    stack_buf[k] = std.ascii.toLower(ch);
                }
                const word_lower = stack_buf[0..word_raw.len];

                const gop = try token_map.getOrPut(word_lower);
                if (!gop.found_existing) {
                    gop.key_ptr.* = try allocator.dupe(u8, word_lower);
                }
            }

            if (word_raw.len >= 3) {
                var gram_len: usize = 3;
                while (gram_len <= 4 and gram_len <= word_raw.len) : (gram_len += 1) {
                    var g_start: usize = 0;
                    while (g_start + gram_len <= word_raw.len) : (g_start += 1) {
                        var g_buf: [4]u8 = undefined;
                        for (word_raw[g_start .. g_start + gram_len], 0..) |ch, k| {
                            g_buf[k] = std.ascii.toLower(ch);
                        }
                        const gram = g_buf[0..gram_len];
                        const gop_gram = try token_map.getOrPut(gram);
                        if (!gop_gram.found_existing) {
                            gop_gram.key_ptr.* = try allocator.dupe(u8, gram);
                        }
                    }
                }
            }
            continue;
        }

        // 2. 中文字符提取 (UTF-8 3字节: 0xE4 ~ 0xE9)
        if (b >= 0xE4 and b <= 0xE9 and i + 2 < text.len) {
            const cjk_char = text[i .. i + 3];
            i += 3;

            const gop_single = try token_map.getOrPut(cjk_char);
            if (!gop_single.found_existing) {
                gop_single.key_ptr.* = try allocator.dupe(u8, cjk_char);
            }

            if (last_cjk) |prev| {
                var bi: [6]u8 = undefined;
                @memcpy(bi[0..3], &prev);
                @memcpy(bi[3..6], cjk_char);
                const gop_bi = try token_map.getOrPut(&bi);
                if (!gop_bi.found_existing) {
                    gop_bi.key_ptr.* = try allocator.dupe(u8, &bi);
                }
            }

            var curr_cjk: [3]u8 = undefined;
            @memcpy(&curr_cjk, cjk_char);
            last_cjk = curr_cjk;
            continue;
        }

        last_cjk = null;
        i += 1;
    }
}

pub fn intersectPostings(a: []const u32, b: []const u32, allocator: std.mem.Allocator) ![]u32 {
    var res = std.ArrayList(u32).init(allocator);
    var i: usize = 0;
    var j: usize = 0;
    while (i < a.len and j < b.len) {
        if (a[i] == b[j]) {
            try res.append(a[i]);
            i += 1;
            j += 1;
        } else if (a[i] < b[j]) {
            i += 1;
        } else {
            j += 1;
        }
    }
    return res.toOwnedSlice();
}

pub const TermRecord = struct {
    term: []const u8,
    doc_freq: u32,
    posting_offset: u32,
    posting_bytes_len: u32,
};

pub const LoadedIndex = struct {
    header: IndexHeader,
    file_bytes: []align(std.heap.page_size_min) u8,
    docs_offsets: []const u32,
    terms_offsets: []const u32,
    allocator: std.mem.Allocator,

    pub fn deinit(self: *LoadedIndex) void {
        std.posix.munmap(self.file_bytes);
    }

    /// 二分查找 Term (微秒级零内存拷贝)
    pub fn findTerm(self: *const LoadedIndex, target_kw: []const u8) ?TermRecord {
        if (self.header.num_terms == 0) return null;
        var low: usize = 0;
        var high: usize = self.header.num_terms;
        while (low < high) {
            const mid = low + (high - low) / 2;
            const t_pos: usize = @intCast(self.header.terms_data_offset + self.terms_offsets[mid]);
            if (t_pos + 2 > self.header.postings_offset) return null;
            const t_len = std.mem.readInt(u16, self.file_bytes[t_pos .. t_pos + 2][0..2], .little);
            const t_str_start = t_pos + 2;
            const t_str_end = t_str_start + t_len;
            if (t_str_end + 12 > self.header.postings_offset) return null;
            const t_str = self.file_bytes[t_str_start..t_str_end];

            const cmp = std.mem.order(u8, target_kw, t_str);
            switch (cmp) {
                .eq => {
                    const doc_freq = std.mem.readInt(u32, self.file_bytes[t_str_end .. t_str_end + 4][0..4], .little);
                    const posting_offset = std.mem.readInt(u32, self.file_bytes[t_str_end + 4 .. t_str_end + 8][0..4], .little);
                    const posting_bytes_len = std.mem.readInt(u32, self.file_bytes[t_str_end + 8 .. t_str_end + 12][0..4], .little);
                    return TermRecord{
                        .term = t_str,
                        .doc_freq = doc_freq,
                        .posting_offset = posting_offset,
                        .posting_bytes_len = posting_bytes_len,
                    };
                },
                .lt => high = mid,
                .gt => low = mid + 1,
            }
        }
        return null;
    }

    /// 解码指定 Term 的倒排链 (有序 docIDs)
    pub fn decodePostings(self: *const LoadedIndex, term_rec: TermRecord, allocator: std.mem.Allocator) ![]u32 {
        var list = try allocator.alloc(u32, term_rec.doc_freq);
        const p_start: usize = @intCast(self.header.postings_offset + term_rec.posting_offset);
        const p_end: usize = p_start + term_rec.posting_bytes_len;
        if (p_end > self.file_bytes.len) return error.CorruptIndex;
        const p_slice = self.file_bytes[p_start..p_end];
        var idx: usize = 0;
        var last_doc: u32 = 0;
        var count: usize = 0;
        while (count < term_rec.doc_freq) : (count += 1) {
            const delta = decodeVarint(p_slice, &idx) orelse return error.CorruptIndex;
            const doc_id = if (count == 0) delta else last_doc + delta;
            list[count] = doc_id;
            last_doc = doc_id;
        }
        return list;
    }

    /// 解码单个文档的元数据
    pub fn getDocMeta(self: *const LoadedIndex, doc_id: u32) !DocMetadata {
        if (doc_id >= self.header.num_docs) return error.CorruptIndex;
        var d_cursor: usize = @intCast(self.header.docs_data_offset + self.docs_offsets[doc_id]);
        if (d_cursor + 24 > self.header.terms_idx_offset) return error.CorruptIndex;

        const mtime = std.mem.readInt(i128, self.file_bytes[d_cursor .. d_cursor + 16][0..16], .little);
        d_cursor += 16;
        const size_bytes = std.mem.readInt(u64, self.file_bytes[d_cursor .. d_cursor + 8][0..8], .little);
        d_cursor += 8;

        var meta: DocMetadata = undefined;
        meta.mtime = mtime;
        meta.size_bytes = size_bytes;

        inline for (.{ "agent", "sid", "ws", "title", "time_str", "path" }) |field| {
            if (d_cursor + 2 > self.header.terms_idx_offset) return error.CorruptIndex;
            const str_len = std.mem.readInt(u16, self.file_bytes[d_cursor .. d_cursor + 2][0..2], .little);
            if (str_len > 1024) return error.CorruptIndex;
            d_cursor += 2;
            if (d_cursor + str_len > self.header.terms_idx_offset) return error.CorruptIndex;
            const s = self.file_bytes[d_cursor .. d_cursor + str_len];
            d_cursor += str_len;
            @field(meta, field) = s;
        }

        return meta;
    }
};

/// 严格校验并打开索引文件 (带损坏容错与零拷贝 mmap)
pub fn openIndex(path: []const u8) ?LoadedIndex {
    const file = std.fs.openFileAbsolute(path, .{}) catch return null;
    defer file.close();

    const stat = file.stat() catch return null;
    if (stat.size < @sizeOf(IndexHeader)) return null;

    const file_bytes = std.posix.mmap(
        null,
        stat.size,
        std.posix.PROT.READ,
        .{ .TYPE = .SHARED },
        file.handle,
        0,
    ) catch return null;
    errdefer std.posix.munmap(file_bytes);

    if (file_bytes.len < @sizeOf(IndexHeader)) return null;
    const header = std.mem.bytesAsValue(IndexHeader, file_bytes[0..@sizeOf(IndexHeader)]).*;

    if (!std.mem.eql(u8, &header.magic, &MAGIC)) return null;
    if (header.version != CURRENT_VERSION) return null;

    // 结构偏移严格递增且边界不越界
    if (header.docs_idx_offset > header.docs_data_offset or
        header.docs_data_offset > header.terms_idx_offset or
        header.terms_idx_offset > header.terms_data_offset or
        header.terms_data_offset > header.postings_offset or
        header.postings_offset > stat.size)
    {
        return null;
    }

    // 偏移表大小检查
    const docs_idx_len: usize = @intCast(header.num_docs * 4);
    const terms_idx_len: usize = @intCast(header.num_terms * 4);
    if (header.docs_idx_offset + docs_idx_len > header.docs_data_offset) return null;
    if (header.terms_idx_offset + terms_idx_len > header.terms_data_offset) return null;

    const docs_idx_start: usize = @intCast(header.docs_idx_offset);
    const docs_offsets_bytes = file_bytes[docs_idx_start .. docs_idx_start + docs_idx_len];
    const docs_offsets = @as([*]const u32, @ptrCast(@alignCast(docs_offsets_bytes.ptr)))[0..header.num_docs];

    const terms_idx_start: usize = @intCast(header.terms_idx_offset);
    const terms_offsets_bytes = file_bytes[terms_idx_start .. terms_idx_start + terms_idx_len];
    const terms_offsets = @as([*]const u32, @ptrCast(@alignCast(terms_offsets_bytes.ptr)))[0..header.num_terms];

    // 校验 offsets 单调性与有效性
    if (header.num_docs > 1) {
        if (docs_offsets[0] != 0 or docs_offsets[header.num_docs - 1] >= (header.terms_idx_offset - header.docs_data_offset)) {
            return null;
        }
    }
    if (header.num_terms > 1) {
        if (terms_offsets[0] != 0 or terms_offsets[header.num_terms - 1] >= (header.postings_offset - header.terms_data_offset)) {
            return null;
        }
    }

    return LoadedIndex{
        .header = header,
        .file_bytes = file_bytes,
        .docs_offsets = docs_offsets,
        .terms_offsets = terms_offsets,
        .allocator = undefined,
    };
}

pub const BuildDocItem = struct {
    meta: DocMetadata,
    tokens: std.ArrayList([]const u8),
};

/// 序列化并原子写盘
pub fn writeIndexFile(
    target_path: []const u8,
    doc_items: []const BuildDocItem,
    allocator: std.mem.Allocator,
) !void {
    // 1. 构建倒排字典
    var term_map = std.StringHashMap(std.ArrayList(u32)).init(allocator);
    defer {
        var it = term_map.iterator();
        while (it.next()) |entry| {
            entry.value_ptr.deinit();
        }
        term_map.deinit();
    }

    for (doc_items, 0..) |doc, d_idx| {
        const doc_id: u32 = @intCast(d_idx);
        for (doc.tokens.items) |t| {
            var gop = try term_map.getOrPut(t);
            if (!gop.found_existing) {
                gop.value_ptr.* = std.ArrayList(u32).init(allocator);
            }
            try gop.value_ptr.append(doc_id);
        }
    }

    // 2. 收集并排序所有 Term
    const TermDocPair = struct {
        term: []const u8,
        doc_ids: []const u32,
    };
    var sorted_terms = try allocator.alloc(TermDocPair, term_map.count());
    defer allocator.free(sorted_terms);

    {
        var idx: usize = 0;
        var it = term_map.iterator();
        while (it.next()) |entry| : (idx += 1) {
            sorted_terms[idx] = .{
                .term = entry.key_ptr.*,
                .doc_ids = entry.value_ptr.items,
            };
        }
    }

    std.mem.sort(TermDocPair, sorted_terms, {}, struct {
        fn lessThan(_: void, a: TermDocPair, b: TermDocPair) bool {
            return std.mem.order(u8, a.term, b.term) == .lt;
        }
    }.lessThan);

    // 3. 构建 Postings 区 buffer
    var postings_buf = std.ArrayList(u8).init(allocator);
    defer postings_buf.deinit();

    const TermMeta = struct {
        posting_offset: u32,
        posting_len: u32,
    };
    var term_metas = try allocator.alloc(TermMeta, sorted_terms.len);
    defer allocator.free(term_metas);

    var vbuf: [16]u8 = undefined;
    for (sorted_terms, 0..) |t_pair, idx| {
        const start_offset: u32 = @intCast(postings_buf.items.len);
        var last_doc: u32 = 0;
        for (t_pair.doc_ids, 0..) |d, d_idx| {
            const delta: u32 = if (d_idx == 0) d else d - last_doc;
            const vlen = encodeVarint(delta, &vbuf);
            try postings_buf.appendSlice(vbuf[0..vlen]);
            last_doc = d;
        }
        const written_len: u32 = @intCast(postings_buf.items.len - start_offset);
        term_metas[idx] = .{
            .posting_offset = start_offset,
            .posting_len = written_len,
        };
    }

    // 4. 构建 Terms Data 区 与 Terms Offsets
    var terms_data_buf = std.ArrayList(u8).init(allocator);
    defer terms_data_buf.deinit();
    var terms_writer = terms_data_buf.writer();

    var terms_offsets = try allocator.alloc(u32, sorted_terms.len);
    defer allocator.free(terms_offsets);

    for (sorted_terms, 0..) |t_pair, idx| {
        terms_offsets[idx] = @intCast(terms_data_buf.items.len);
        try terms_writer.writeInt(u16, @intCast(t_pair.term.len), .little);
        try terms_writer.writeAll(t_pair.term);
        try terms_writer.writeInt(u32, @intCast(t_pair.doc_ids.len), .little);
        try terms_writer.writeInt(u32, term_metas[idx].posting_offset, .little);
        try terms_writer.writeInt(u32, term_metas[idx].posting_len, .little);
    }

    // 5. 构建 Docs Data 区 与 Docs Offsets
    var docs_data_buf = std.ArrayList(u8).init(allocator);
    defer docs_data_buf.deinit();
    var docs_writer = docs_data_buf.writer();

    var docs_offsets = try allocator.alloc(u32, doc_items.len);
    defer allocator.free(docs_offsets);

    for (doc_items, 0..) |doc, idx| {
        docs_offsets[idx] = @intCast(docs_data_buf.items.len);
        try docs_writer.writeInt(i128, doc.meta.mtime, .little);
        try docs_writer.writeInt(u64, doc.meta.size_bytes, .little);

        inline for (.{ "agent", "sid", "ws", "title", "time_str", "path" }) |field| {
            const s = @field(doc.meta, field);
            try docs_writer.writeInt(u16, @intCast(s.len), .little);
            try docs_writer.writeAll(s);
        }
    }

    // 6. 规划文件布局并严格按 4 字节对齐
    const header_size: u64 = @sizeOf(IndexHeader);
    const docs_idx_offset: u64 = (header_size + 3) & ~@as(u64, 3);
    const docs_idx_bytes: u64 = doc_items.len * 4;
    const docs_data_offset: u64 = (docs_idx_offset + docs_idx_bytes + 3) & ~@as(u64, 3);
    const terms_idx_offset: u64 = (docs_data_offset + docs_data_buf.items.len + 3) & ~@as(u64, 3);
    const terms_idx_bytes: u64 = sorted_terms.len * 4;
    const terms_data_offset: u64 = (terms_idx_offset + terms_idx_bytes + 3) & ~@as(u64, 3);
    const postings_offset: u64 = (terms_data_offset + terms_data_buf.items.len + 3) & ~@as(u64, 3);

    var header = IndexHeader{
        .magic = MAGIC,
        .version = CURRENT_VERSION,
        .num_docs = @intCast(doc_items.len),
        .num_terms = @intCast(sorted_terms.len),
        .created_at_ms = std.time.milliTimestamp(),
        .docs_idx_offset = docs_idx_offset,
        .docs_data_offset = docs_data_offset,
        .terms_idx_offset = terms_idx_offset,
        .terms_data_offset = terms_data_offset,
        .postings_offset = postings_offset,
        .checksum = 0,
    };

    var final_buf = std.ArrayList(u8).init(allocator);
    defer final_buf.deinit();

    try final_buf.appendSlice(std.mem.asBytes(&header));
    while ((final_buf.items.len % 4) != 0) try final_buf.append(0);

    // 写 Docs Offsets
    for (docs_offsets) |off| {
        var obuf: [4]u8 = undefined;
        std.mem.writeInt(u32, &obuf, off, .little);
        try final_buf.appendSlice(&obuf);
    }
    while ((final_buf.items.len % 4) != 0) try final_buf.append(0);

    try final_buf.appendSlice(docs_data_buf.items);
    while ((final_buf.items.len % 4) != 0) try final_buf.append(0);

    // 写 Terms Offsets
    for (terms_offsets) |off| {
        var obuf: [4]u8 = undefined;
        std.mem.writeInt(u32, &obuf, off, .little);
        try final_buf.appendSlice(&obuf);
    }
    while ((final_buf.items.len % 4) != 0) try final_buf.append(0);

    try final_buf.appendSlice(terms_data_buf.items);
    while ((final_buf.items.len % 4) != 0) try final_buf.append(0);
    try final_buf.appendSlice(postings_buf.items);

    const dir_path = std.fs.path.dirname(target_path) orelse "/root/.agent";
    std.fs.makeDirAbsolute(dir_path) catch {};

    const tmp_path = try std.fmt.allocPrint(allocator, "{s}.tmp.{d}", .{ target_path, std.time.milliTimestamp() });
    defer allocator.free(tmp_path);

    const f = try std.fs.createFileAbsolute(tmp_path, .{});
    try f.writeAll(final_buf.items);
    f.close();

    try std.fs.renameAbsolute(tmp_path, target_path);
}

/// 提取单个会话的全部文本 tokens
pub fn extractSessionTokens(
    agent: []const u8,
    sid: []const u8,
    path: []const u8,
    title: []const u8,
    tokens: *std.ArrayList([]const u8),
    allocator: std.mem.Allocator,
) !void {
    var token_map = std.StringHashMap(void).init(allocator);
    defer {
        var it = token_map.keyIterator();
        while (it.next()) |k| {
            allocator.free(k.*);
        }
        token_map.deinit();
    }

    try tokenizeText(title, &token_map, allocator);

    if (std.mem.eql(u8, agent, "agy")) {
        const transcript_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{sid});
        defer allocator.free(transcript_path);

        if (std.fs.openFileAbsolute(transcript_path, .{})) |tf| {
            defer tf.close();
            if (tf.readToEndAlloc(allocator, 50 * 1024 * 1024)) |t_content| {
                defer allocator.free(t_content);
                try tokenizeText(t_content, &token_map, allocator);
            } else |_| {}
        } else |_| {}

        const db_path_z = try allocator.dupeZ(u8, path);
        defer allocator.free(db_path_z);

        var db_ptr: ?*main.c.sqlite3 = null;
        if (main.c.sqlite3_open_v2(db_path_z, &db_ptr, main.c.SQLITE_OPEN_READONLY, null) == main.c.SQLITE_OK) {
            if (db_ptr) |db| {
                defer _ = main.c.sqlite3_close(db);
                var stmt: ?*main.c.sqlite3_stmt = null;
                const sql = "SELECT step_payload FROM steps;";
                if (main.c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == main.c.SQLITE_OK) {
                    if (stmt) |s| {
                        defer _ = main.c.sqlite3_finalize(s);
                        while (main.c.sqlite3_step(s) == main.c.SQLITE_ROW) {
                            const blob_ptr = main.c.sqlite3_column_blob(s, 0);
                            const bytes = main.c.sqlite3_column_bytes(s, 0);
                            if (blob_ptr != null and bytes > 0) {
                                const blob_len = @as(usize, @intCast(bytes));
                                const blob = @as([*]const u8, @ptrCast(blob_ptr.?))[0..blob_len];
                                try tokenizeText(blob, &token_map, allocator);
                            }
                        }
                    }
                }
            }
        }
    } else {
        if (std.fs.openFileAbsolute(path, .{})) |file| {
            defer file.close();
            if (file.readToEndAlloc(allocator, 50 * 1024 * 1024)) |content| {
                defer allocator.free(content);
                try tokenizeText(content, &token_map, allocator);
            } else |_| {}
        } else |_| {}
    }

    var it = token_map.keyIterator();
    while (it.next()) |k| {
        try tokens.append(try allocator.dupe(u8, k.*));
    }
}

pub const ScanSessionItem = struct {
    agent: []const u8,
    sid: []const u8,
    mtime: i128,
    time_str: []const u8,
    ws: []const u8,
    size_bytes: u64,
    title: []const u8,
    path: []const u8,
};

/// 扫描系统中所有会话的基础元数据 (极速 stat 模式，1ms)
pub fn scanAllSessionsMeta(list: *std.ArrayList(ScanSessionItem), allocator: std.mem.Allocator) !void {
    var active_map = std.StringHashMap(i128).init(allocator);
    defer active_map.deinit();
    {
        var sdir = std.fs.openDirAbsolute("/root/.codebuddy/sessions", .{ .iterate = true }) catch null;
        if (sdir) |*sd| {
            defer sd.close();
            var it = sd.iterate();
            const now_ms: i64 = std.time.milliTimestamp();
            while (it.next() catch null) |e| {
                if (e.kind != .file or !std.mem.endsWith(u8, e.name, ".json")) continue;
                const meta_path = try std.fmt.allocPrint(allocator, "/root/.codebuddy/sessions/{s}", .{e.name});
                defer allocator.free(meta_path);
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

                const hb_key = "\"lastHeartbeat\":";
                var hb_ms: i64 = 0;
                if (std.mem.indexOf(u8, content, hb_key)) |hi| {
                    const num_start = hi + hb_key.len;
                    var num_end = num_start;
                    while (num_end < content.len and content[num_end] >= '0' and content[num_end] <= '9') : (num_end += 1) {}
                    if (num_end > num_start) {
                        hb_ms = std.fmt.parseInt(i64, content[num_start..num_end], 10) catch 0;
                    }
                }
                if (now_ms - hb_ms <= 5 * 60 * 1000 and hb_ms > 0) {
                    const sid_dupe = try allocator.dupe(u8, sess_id);
                    try active_map.put(sid_dupe, @as(i128, hb_ms) * 1_000_000);
                }
            }
        }
    }

    var summary_map = std.StringHashMap(struct { preview: []const u8, ws: []const u8, steps: i32 }).init(allocator);
    defer {
        var it = summary_map.iterator();
        while (it.next()) |entry| {
            allocator.free(entry.key_ptr.*);
            allocator.free(entry.value_ptr.preview);
            allocator.free(entry.value_ptr.ws);
        }
        summary_map.deinit();
    }
    {
        var db_ptr: ?*main.c.sqlite3 = null;
        if (main.c.sqlite3_open_v2("/root/.gemini/antigravity-cli/conversation_summaries.db", &db_ptr, main.c.SQLITE_OPEN_READONLY, null) == main.c.SQLITE_OK) {
            if (db_ptr) |db| {
                defer _ = main.c.sqlite3_close(db);
                var stmt: ?*main.c.sqlite3_stmt = null;
                const sql = "SELECT conversation_id, preview, step_count, workspace_uris FROM conversation_summaries";
                if (main.c.sqlite3_prepare_v2(db, sql, -1, &stmt, null) == main.c.SQLITE_OK) {
                    if (stmt) |s| {
                        defer _ = main.c.sqlite3_finalize(s);
                        while (main.c.sqlite3_step(s) == main.c.SQLITE_ROW) {
                            const cid_c = main.c.sqlite3_column_text(s, 0);
                            const prev_c = main.c.sqlite3_column_text(s, 1);
                            const steps = main.c.sqlite3_column_int(s, 2);
                            const ws_c = main.c.sqlite3_column_text(s, 3);
                            if (cid_c == null) continue;
                            const cid = try allocator.dupe(u8, std.mem.span(cid_c.?));
                            const prev = if (prev_c) |p| try allocator.dupe(u8, std.mem.span(p)) else try allocator.dupe(u8, "");
                            const ws_str = if (ws_c) |w| try allocator.dupe(u8, main.cleanWorkspace(std.mem.span(w))) else try allocator.dupe(u8, "root");
                            try summary_map.put(cid, .{ .preview = prev, .ws = ws_str, .steps = steps });
                        }
                    }
                }
            }
        }

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
                        if (item_sum.steps == 0 and item_sum.preview.len == 0) continue;
                        title = if (item_sum.preview.len > 0) main.truncateUtf8Safe(item_sum.preview, 36) else "(空会话)";
                        ws_str = item_sum.ws;
                    } else {
                        const trans_path = try std.fmt.allocPrint(allocator, "/root/.gemini/antigravity-cli/brain/{s}/.system_generated/logs/transcript.jsonl", .{sid});
                        defer allocator.free(trans_path);
                        const trans_title = main.extractJsonlTitle(trans_path, allocator) catch null;
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

                    var time_buf: [32]u8 = undefined;
                    const time_str = main.formatEpochSeconds(@intCast(@divFloor(stat_info.mtime, 1_000_000_000)), &time_buf);

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

    try scanDirJsonlMeta("qoder", "/root/.qoder/projects", null, list, allocator);
    try scanDirJsonlMeta("codebuddy", "/root/.codebuddy/projects", &active_map, list, allocator);

    {
        var base_dir = std.fs.openDirAbsolute("/root/.reasonix/projects", .{ .iterate = true }) catch null;
        if (base_dir) |*bdir| {
            defer bdir.close();
            var b_iter = bdir.iterate();
            while (b_iter.next() catch null) |entry| {
                if (entry.kind == .directory) {
                    const sess_dir_path = try std.fmt.allocPrint(allocator, "/root/.reasonix/projects/{s}/sessions", .{entry.name});
                    defer allocator.free(sess_dir_path);
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
                            const ws = try allocator.dupe(u8, main.cleanWorkspace(entry.name));

                            var tbuf: [32]u8 = undefined;
                            const time_str = main.formatEpochSeconds(@intCast(@divFloor(stat_info.mtime, 1_000_000_000)), &tbuf);

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
}

fn scanDirJsonlMeta(
    agent_name: []const u8,
    root_dir: []const u8,
    active_map: ?*const std.StringHashMap(i128),
    list: *std.ArrayList(ScanSessionItem),
    allocator: std.mem.Allocator,
) !void {
    var base_dir = std.fs.openDirAbsolute(root_dir, .{ .iterate = true }) catch return;
    defer base_dir.close();
    var b_iter = base_dir.iterate();
    while (b_iter.next() catch null) |entry| {
        if (entry.kind != .directory) continue;
        var sub_dir = base_dir.openDir(entry.name, .{ .iterate = true }) catch continue;
        defer sub_dir.close();
        var s_iter = sub_dir.iterate();
        while (s_iter.next() catch null) |f_entry| {
            if (f_entry.kind != .file or !std.mem.endsWith(u8, f_entry.name, ".jsonl")) continue;
            const full_path = try std.fmt.allocPrint(allocator, "{s}/{s}/{s}", .{ root_dir, entry.name, f_entry.name });
            const stat = std.fs.openFileAbsolute(full_path, .{}) catch continue;
            const stat_info = stat.stat() catch {
                stat.close();
                continue;
            };
            stat.close();

            const raw_sid = if (f_entry.name.len > 6) f_entry.name[0 .. f_entry.name.len - 6] else f_entry.name;
            const sid = try allocator.dupe(u8, raw_sid);
            const ws = try allocator.dupe(u8, main.cleanWorkspace(entry.name));

            var title = main.extractJsonlTitle(full_path, allocator) catch "(空会话)";
            if (std.mem.eql(u8, title, "(空会话)") or std.mem.startsWith(u8, title, "(/")) {
                continue;
            }

            var eff_mtime = stat_info.mtime;
            if (active_map) |act_map| {
                var tail_buf: [8192]u8 = undefined;
                if (main.readFileTail(full_path, &tail_buf)) |tail| {
                    if (act_map.get(sid)) |hb_ns| {
                        if (hb_ns > eff_mtime) eff_mtime = hb_ns;
                        title = try std.fmt.allocPrint(allocator, "⚡ {s}", .{title});
                    } else if (main.lastLineIsChangeSession(tail)) {
                        if (main.lastRealTimestampMs(tail)) |ts_ms| {
                            const real_ns: i128 = @as(i128, ts_ms) * std.time.ns_per_ms;
                            if (real_ns < eff_mtime) eff_mtime = real_ns;
                        }
                    }
                }
            }

            var tbuf: [32]u8 = undefined;
            const time_str = main.formatEpochSeconds(@intCast(@divFloor(eff_mtime, 1_000_000_000)), &tbuf);

            try list.append(.{
                .agent = agent_name,
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

/// 构建或增量更新倒排索引 (支持按段增量、mtime变动检测与合并)
pub fn buildOrUpdateIndex(force: bool, allocator: std.mem.Allocator) !void {
    const stdout = std.io.getStdOut().writer();

    var current_sessions = std.ArrayList(ScanSessionItem).init(allocator);
    defer current_sessions.deinit();
    try scanAllSessionsMeta(&current_sessions, allocator);

    var old_idx_opt = if (!force) openIndex(INDEX_PATH) else null;
    defer {
        if (old_idx_opt) |*old_idx| old_idx.deinit();
    }

    var build_items = std.ArrayList(BuildDocItem).init(allocator);
    defer build_items.deinit();

    if (old_idx_opt) |*old_idx| {
        // 构建路径到 old_doc_id 的映射
        var old_map = std.StringHashMap(u32).init(allocator);
        defer old_map.deinit();
        for (0..old_idx.header.num_docs) |d_id_usize| {
            const d_id: u32 = @intCast(d_id_usize);
            if (old_idx.getDocMeta(d_id)) |old_d| {
                try old_map.put(old_d.path, d_id);
            } else |_| {}
        }

        var changed_count: usize = 0;
        for (current_sessions.items) |sess| {
            if (old_map.get(sess.path)) |old_d_id| {
                if (old_idx.getDocMeta(old_d_id)) |old_d| {
                    if (old_d.mtime != sess.mtime or old_d.size_bytes != sess.size_bytes) {
                        changed_count += 1;
                    }
                } else |_| {
                    changed_count += 1;
                }
            } else {
                changed_count += 1;
            }
        }

        if (changed_count == 0 and current_sessions.items.len == old_idx.header.num_docs) {
            try stdout.print("\x1b[32m[lane-search] 索引已是最新 (0 个变动，共 {d} 会话)\x1b[0m\n", .{old_idx.header.num_docs});
            return;
        }

        try stdout.print("\x1b[33m[lane-search] 增量更新: 检测到 {d} 个会话变动，只重建变动段并合并\x1b[0m\n", .{changed_count});

        // 提取旧索引中每个文档的 tokens
        var doc_tokens_map = std.AutoHashMap(u32, std.ArrayList([]const u8)).init(allocator);
        defer {
            var it = doc_tokens_map.valueIterator();
            while (it.next()) |arr| {
                arr.deinit();
            }
            doc_tokens_map.deinit();
        }

        for (0..old_idx.header.num_terms) |t_idx| {
            const t_pos = old_idx.header.terms_data_offset + old_idx.terms_offsets[t_idx];
            const t_len = std.mem.readInt(u16, old_idx.file_bytes[t_pos .. t_pos + 2][0..2], .little);
            const t_str = old_idx.file_bytes[t_pos + 2 .. t_pos + 2 + t_len];
            const d_freq = std.mem.readInt(u32, old_idx.file_bytes[t_pos + 2 + t_len .. t_pos + 6 + t_len][0..4], .little);
            const p_offset = std.mem.readInt(u32, old_idx.file_bytes[t_pos + 6 + t_len .. t_pos + 10 + t_len][0..4], .little);
            const p_bytes_len = std.mem.readInt(u32, old_idx.file_bytes[t_pos + 10 + t_len .. t_pos + 14 + t_len][0..4], .little);

            const doc_ids = try old_idx.decodePostings(.{
                .term = t_str,
                .doc_freq = d_freq,
                .posting_offset = p_offset,
                .posting_bytes_len = p_bytes_len,
            }, allocator);
            defer allocator.free(doc_ids);

            for (doc_ids) |d_id| {
                var gop = try doc_tokens_map.getOrPut(d_id);
                if (!gop.found_existing) {
                    gop.value_ptr.* = std.ArrayList([]const u8).init(allocator);
                }
                try gop.value_ptr.append(t_str);
            }
        }

        // 遍历所有当前会话
        for (current_sessions.items) |sess| {
            if (old_map.get(sess.path)) |old_d_id| {
                if (old_idx.getDocMeta(old_d_id)) |old_d| {
                    if (old_d.mtime == sess.mtime and old_d.size_bytes == sess.size_bytes) {
                        // 未变动，复用旧 tokens
                        if (doc_tokens_map.get(old_d_id)) |old_tokens| {
                            try build_items.append(.{
                                .meta = .{
                                    .agent = sess.agent,
                                    .sid = sess.sid,
                                    .mtime = sess.mtime,
                                    .time_str = sess.time_str,
                                    .ws = sess.ws,
                                    .size_bytes = sess.size_bytes,
                                    .title = sess.title,
                                    .path = sess.path,
                                },
                                .tokens = old_tokens,
                            });
                            continue;
                        }
                    }
                } else |_| {}
            }

            // 新增或变动，重新提取 tokens
            var tokens = std.ArrayList([]const u8).init(allocator);
            try extractSessionTokens(sess.agent, sess.sid, sess.path, sess.title, &tokens, allocator);
            try build_items.append(.{
                .meta = .{
                    .agent = sess.agent,
                    .sid = sess.sid,
                    .mtime = sess.mtime,
                    .time_str = sess.time_str,
                    .ws = sess.ws,
                    .size_bytes = sess.size_bytes,
                    .title = sess.title,
                    .path = sess.path,
                },
                .tokens = tokens,
            });
        }
    } else {
        // 全量构建
        try stdout.print("\x1b[36m[lane-search] 全量构建倒排索引 (扫描 {d} 个会话)...\x1b[0m\n", .{current_sessions.items.len});
        for (current_sessions.items) |sess| {
            var tokens = std.ArrayList([]const u8).init(allocator);
            try extractSessionTokens(sess.agent, sess.sid, sess.path, sess.title, &tokens, allocator);
            try build_items.append(.{
                .meta = .{
                    .agent = sess.agent,
                    .sid = sess.sid,
                    .mtime = sess.mtime,
                    .time_str = sess.time_str,
                    .ws = sess.ws,
                    .size_bytes = sess.size_bytes,
                    .title = sess.title,
                    .path = sess.path,
                },
                .tokens = tokens,
            });
        }
    }

    try writeIndexFile(INDEX_PATH, build_items.items, allocator);
    try stdout.print("\x1b[32m[lane-search] 索引构建完成，已安全落盘至 {s}\x1b[0m\n", .{INDEX_PATH});
}

/// 极速倒排索引检索 (微秒级响应，二分词表 + 倒排链求交 + 损坏自愈/回退)
pub fn searchWithIndex(
    keywords: []const []const u8,
    allocator: std.mem.Allocator,
) !?std.ArrayList(main.SessionItem) {
    if (keywords.len == 0) return null;

    var loaded = openIndex(INDEX_PATH) orelse return null;
    defer loaded.deinit();

    var current_matched: ?[]u32 = null;
    defer {
        if (current_matched) |m| allocator.free(m);
    }

    for (keywords) |raw_kw| {
        var kw_lower_buf: [256]u8 = undefined;
        if (raw_kw.len >= kw_lower_buf.len) return null;
        for (raw_kw, 0..) |c_ch, idx| {
            kw_lower_buf[idx] = std.ascii.toLower(c_ch);
        }
        const kw_lower = kw_lower_buf[0..raw_kw.len];

        const term_rec = loaded.findTerm(kw_lower) orelse {
            // 未命中词项：返回 null，让外层自动回退到传统 grep 流式扫描
            return null;
        };

        const post = loaded.decodePostings(term_rec, allocator) catch return null;

        if (current_matched == null) {
            current_matched = post;
        } else {
            const next_intersected = intersectPostings(current_matched.?, post, allocator) catch {
                allocator.free(post);
                return null;
            };
            allocator.free(post);
            allocator.free(current_matched.?);
            current_matched = next_intersected;
        }
    }

    const matched_ids = current_matched orelse return null;

    var result_list = std.ArrayList(main.SessionItem).init(allocator);
    for (matched_ids) |d_id| {
        if (d_id >= loaded.header.num_docs) return null;
        const d = loaded.getDocMeta(d_id) catch return null;
        try result_list.append(.{
            .agent = try allocator.dupe(u8, d.agent),
            .sid = try allocator.dupe(u8, d.sid),
            .mtime = d.mtime,
            .time_str = try allocator.dupe(u8, d.time_str),
            .ws = try allocator.dupe(u8, d.ws),
            .size_bytes = d.size_bytes,
            .title = try allocator.dupe(u8, d.title),
            .path = try allocator.dupe(u8, d.path),
        });
    }

    return result_list;
}

test "varint encode and decode" {
    var buf: [16]u8 = undefined;
    const test_vals = [_]u32{ 0, 1, 127, 128, 255, 300, 16384, 2097151, 2097152, 4294967295 };
    for (test_vals) |val| {
        const len = encodeVarint(val, &buf);
        var idx: usize = 0;
        const decoded = decodeVarint(buf[0..len], &idx);
        try std.testing.expect(decoded != null);
        try std.testing.expectEqual(val, decoded.?);
        try std.testing.expectEqual(len, idx);
    }
}

test "tokenizer ascii and chinese bigram" {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    var token_map = std.StringHashMap(void).init(allocator);
    defer {
        var it = token_map.keyIterator();
        while (it.next()) |k| {
            allocator.free(k.*);
        }
        token_map.deinit();
    }

    const sample = "WAL TTFT 心跳 测试 突刺 herdr\\nfunes hello_world";
    try tokenizeText(sample, &token_map, allocator);

    try std.testing.expect(token_map.contains("wal"));
    try std.testing.expect(token_map.contains("ttft"));
    try std.testing.expect(token_map.contains("herdr"));
    try std.testing.expect(token_map.contains("funes"));
    try std.testing.expect(token_map.contains("hello_world"));

    try std.testing.expect(token_map.contains("心跳"));
    try std.testing.expect(token_map.contains("测试"));
    try std.testing.expect(token_map.contains("突刺"));
}

test "postings intersection AND logic" {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    const a = [_]u32{ 1, 3, 5, 7, 9, 11, 15 };
    const b = [_]u32{ 2, 3, 7, 10, 15, 20 };
    const expected = [_]u32{ 3, 7, 15 };

    const intersected = try intersectPostings(&a, &b, allocator);
    defer allocator.free(intersected);

    try std.testing.expectEqualSlices(u32, &expected, intersected);
}

test "index build and search roundtrip" {
    var gpa = std.heap.GeneralPurposeAllocator(.{}){};
    defer _ = gpa.deinit();
    const allocator = gpa.allocator();

    const tmp_file = "/tmp/test_roundtrip.idx";
    defer std.fs.deleteFileAbsolute(tmp_file) catch {};

    var doc1_tokens = std.ArrayList([]const u8).init(allocator);
    defer doc1_tokens.deinit();
    try doc1_tokens.append("wal");
    try doc1_tokens.append("herdr");

    var doc2_tokens = std.ArrayList([]const u8).init(allocator);
    defer doc2_tokens.deinit();
    try doc2_tokens.append("wal");
    try doc2_tokens.append("心跳");

    const docs = [_]BuildDocItem{
        .{
            .meta = .{
                .agent = "codebuddy",
                .sid = "01a09afb",
                .mtime = 123456789,
                .time_str = "2026-09-14 11:00",
                .ws = "test-ws",
                .size_bytes = 1024,
                .title = "测试会话1",
                .path = "/tmp/test1.jsonl",
            },
            .tokens = doc1_tokens,
        },
        .{
            .meta = .{
                .agent = "agy",
                .sid = "41309b36",
                .mtime = 123456790,
                .time_str = "2026-09-14 11:05",
                .ws = "metax-workbench",
                .size_bytes = 2048,
                .title = "测试会话2",
                .path = "/tmp/test2.db",
            },
            .tokens = doc2_tokens,
        },
    };

    try writeIndexFile(tmp_file, &docs, allocator);

    var loaded = openIndex(tmp_file).?;
    defer loaded.deinit();

    try std.testing.expectEqual(@as(u32, 2), loaded.header.num_docs);
    try std.testing.expectEqual(@as(u32, 3), loaded.header.num_terms);

    const wal_rec = loaded.findTerm("wal");
    try std.testing.expect(wal_rec != null);
    try std.testing.expectEqual(@as(u32, 2), wal_rec.?.doc_freq);
    const wal_postings = try loaded.decodePostings(wal_rec.?, allocator);
    defer allocator.free(wal_postings);
    try std.testing.expectEqualSlices(u32, &[_]u32{ 0, 1 }, wal_postings);

    const zh_rec = loaded.findTerm("心跳");
    try std.testing.expect(zh_rec != null);
    try std.testing.expectEqual(@as(u32, 1), zh_rec.?.doc_freq);
    const zh_postings = try loaded.decodePostings(zh_rec.?, allocator);
    defer allocator.free(zh_postings);
    try std.testing.expectEqualSlices(u32, &[_]u32{1}, zh_postings);
}
