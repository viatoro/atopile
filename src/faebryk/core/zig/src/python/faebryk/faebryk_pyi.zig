const std = @import("std");
const faebryk = @import("faebryk");
const pyzig = @import("pyzig");

pub fn make_pyi(allocator: std.mem.Allocator, io: std.Io, output_dir: []const u8, source_dir: []const u8) !void {
    std.Io.Dir.cwd().createDirPath(io, output_dir) catch |err| {
        if (err != error.PathAlreadyExists) return err;
    };

    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "composition", faebryk.composition, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "interface", faebryk.interface, output_dir, source_dir);
    //try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "module", faebryk.module, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "node_type", faebryk.node_type, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "next", faebryk.next, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "typegraph", faebryk.typegraph, output_dir, source_dir);
    //try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "parameter", faebryk.parameter, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "linker", faebryk.linker, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "pointer", faebryk.pointer, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "trait", faebryk.trait, output_dir, source_dir);
    try pyzig.pyi.PyiGenerator.manualModuleStub(allocator, io, "operand", faebryk.operand, output_dir, source_dir);
}
