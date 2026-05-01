const std = @import("std");

fn make_pyi(allocator: std.mem.Allocator, io: std.Io, output_dir: []const u8, comptime T: type, comptime name: []const u8, source_dir: []const u8) !void {
    const out = try std.fs.path.join(allocator, &.{ output_dir, name });
    defer allocator.free(out);
    const source_nested = try std.fs.path.join(allocator, &.{ source_dir, name });
    defer allocator.free(source_nested);
    try T.make_pyi(allocator, io, out, source_nested);
}

pub fn main(init: std.process.Init) !void {
    const allocator = init.gpa;
    const io = init.io;

    const args = try init.minimal.args.toSlice(init.arena.allocator());
    if (args.len < 3) {
        std.debug.print("Usage: {s} <output_dir> <source_dir>\n", .{args[0]});
        return error.InvalidUsage;
    }

    const root_output_dir = args[1];
    const output_dir = try std.fs.path.join(allocator, &.{ root_output_dir, "gen" });
    defer allocator.free(output_dir);

    const source_dir = args[2];

    // delete output directory
    std.Io.Dir.cwd().deleteTree(io, output_dir) catch |err| {
        if (err != error.FileNotFound) return err;
    };

    // Ensure output directory exists
    std.Io.Dir.cwd().createDirPath(io, root_output_dir) catch |err| {
        if (err != error.PathAlreadyExists) return err;
    };

    // TODO: instead of giving responsibility to modules just directly use pyigenerator here
    // But first need to make pyigenerator better to do more fancy stuff

    // sexp is slow
    const sexp_pyi = @import("sexp/sexp_pyi.zig");
    try make_pyi(allocator, io, output_dir, sexp_pyi, "sexp", source_dir);
    const graph_pyi = @import("graph/graph_pyi.zig");
    try make_pyi(allocator, io, output_dir, graph_pyi, "graph", source_dir);
    const faebryk_pyi = @import("faebryk/faebryk_pyi.zig");
    try make_pyi(allocator, io, output_dir, faebryk_pyi, "faebryk", source_dir);
}
