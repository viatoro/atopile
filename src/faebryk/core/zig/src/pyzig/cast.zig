const std = @import("std");
const builtin = @import("builtin");

fn checkAlign(comptime T: type, addr: usize) void {
    // Opaque types have no known alignment; skip the check.
    if (@typeInfo(T) == .@"opaque") return;
    if (builtin.mode == .Debug or builtin.mode == .ReleaseSafe) {
        std.debug.assert(addr % @alignOf(T) == 0);
    }
}

fn addrOf(p: anytype) usize {
    const info = @typeInfo(@TypeOf(p));
    switch (info) {
        .optional => {
            std.debug.assert(p != null);
            return @intFromPtr(p.?);
        },
        .pointer => return @intFromPtr(p),
        else => @compileError("cast helper expects pointer or optional pointer, got " ++ @typeName(@TypeOf(p))),
    }
}

/// Downcast a nullable opaque pointer (or similar) to `*T`, asserting non-null and
/// alignment in Debug/ReleaseSafe. In release this compiles to the same code as
/// `@ptrCast(@alignCast(...))`.
///
/// Use for callback-vtable contexts (`?*anyopaque`) and Python object downcasts
/// (`?*py.PyObject`). Do NOT use when provenance cannot guarantee `@alignOf(T)`.
pub fn ctx(comptime T: type, p: anytype) *T {
    checkAlign(T, addrOf(p));
    const info = @typeInfo(@TypeOf(p));
    return switch (info) {
        .optional => @ptrCast(@alignCast(p.?)),
        .pointer => @ptrCast(@alignCast(p)),
        else => unreachable,
    };
}

/// Alias of `ctx` for non-nullable pointers; provided for readability.
pub fn ptr(comptime T: type, p: anytype) *T {
    return ctx(T, p);
}

/// Alias of `ctx` for re-aligning already-typed but under-aligned pointers.
pub fn alignedPtr(comptime T: type, p: anytype) *T {
    return ctx(T, p);
}
