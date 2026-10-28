pub const SQLITE_OK = 0;
pub const SQLITE_ROW = 100;
pub const SQLITE_DONE = 101;
pub const SQLITE_OPEN_READONLY = 0x00000001;
pub const SQLITE_OPEN_READWRITE = 0x00000002;

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
