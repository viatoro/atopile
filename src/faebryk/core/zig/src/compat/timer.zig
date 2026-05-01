//! Minimal monotonic timer shim.
//!
//! Zig 0.16 removed `std.time.Timer` with no std-level replacement. This shim
//! keeps the same `start()`/`read()` surface (read returns nanoseconds since
//! start) so benchmark and stress-test code doesn't need restructuring.

const std = @import("std");
const builtin = @import("builtin");

pub const MonoTimer = struct {
    start_ns: u64,

    pub fn start() MonoTimer {
        return .{ .start_ns = nowNs() };
    }

    pub fn read(self: *MonoTimer) u64 {
        return nowNs() -| self.start_ns;
    }

    pub fn reset(self: *MonoTimer) void {
        self.start_ns = nowNs();
    }

    fn nowNs() u64 {
        if (comptime builtin.os.tag == .windows) {
            const w = std.os.windows;
            var counter: w.LARGE_INTEGER = undefined;
            var freq: w.LARGE_INTEGER = undefined;
            _ = w.ntdll.RtlQueryPerformanceCounter(&counter);
            _ = w.ntdll.RtlQueryPerformanceFrequency(&freq);
            const c: u64 = @intCast(counter);
            const f: u64 = @intCast(freq);
            if (f == 0) return 0;
            // Split to avoid u64 overflow on c*1e9.
            const sec = c / f;
            const rem = c % f;
            return sec * std.time.ns_per_s + (rem * std.time.ns_per_s) / f;
        } else {
            var ts: std.posix.timespec = undefined;
            const rc = std.posix.system.clock_gettime(.MONOTONIC, &ts);
            if (std.posix.errno(rc) != .SUCCESS) return 0;
            const sec: u64 = @intCast(ts.sec);
            const nsec: u64 = @intCast(ts.nsec);
            return sec * std.time.ns_per_s + nsec;
        }
    }
};
