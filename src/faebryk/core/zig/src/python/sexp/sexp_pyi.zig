const std = @import("std");
const pyzig = @import("pyzig");
const sexp = @import("sexp");

fn generateModuleStub(allocator: std.mem.Allocator, io: std.Io, comptime name: []const u8, comptime T: type, comptime typename: []const u8, output_dir: []const u8) !void {
    var generator = pyzig.pyi.PyiGenerator.init(allocator);
    defer generator.deinit();

    const content = try generator.generate(T);
    defer allocator.free(content);

    // Create the output file path
    var path_buf: [256]u8 = undefined;
    const file_path = try std.fmt.bufPrint(&path_buf, "{s}/{s}.pyi", .{ output_dir, name });

    // Write the content to the file
    const file = try std.Io.Dir.cwd().createFile(io, file_path, .{});
    defer file.close(io);

    const import_root = "from faebryk.core.zig.gen.sexp";

    // Hack: Footprint imports some types from pcb
    if (std.mem.eql(u8, name, "footprint")) {
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".pcb import Xyr, Property, FpText, Line, Arc, Circle, Rect, Polygon, Pad, Model, E_Attr\n");
    } else if (std.mem.eql(u8, name, "symbol")) {
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".schematic import Symbol\n");
    } else if (std.mem.eql(u8, name, "schematic")) {
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".pcb import Xy, Xyr, Wh, Effects\n");
    } else if (std.mem.eql(u8, name, "footprint_v5")) {
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".pcb import FpText, ModelXyz, Pad, Polygon, Property, Xy, Xyr, E_Attr\n");
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".footprint import Tags\n");
    } else if (std.mem.eql(u8, name, "symbol_v6")) {
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".pcb import Xy\n");
        try file.writeStreamingAll(io, import_root);
        try file.writeStreamingAll(io, ".schematic import Polyline, Rect, SymbolPin, Fill, Stroke, Property, PinNames, Arc\n");
    }
    try file.writeStreamingAll(io, content);

    // Add module-specific functions if needed
    try file.writeStreamingAll(io, "\n");
    try file.writeStreamingAll(io, "# Module-level functions\n");
    try file.writeStreamingAll(io, std.fmt.comptimePrint("def loads(data: str) -> {s}: ...\n", .{typename}));
    try file.writeStreamingAll(io, std.fmt.comptimePrint("def dumps(obj: {s}) -> str: ...\n", .{typename}));
}

pub fn make_pyi(allocator: std.mem.Allocator, io: std.Io, output_dir: []const u8, source_dir: []const u8) !void {
    _ = source_dir;
    // Ensure output directory exists
    std.Io.Dir.cwd().createDirPath(io, output_dir) catch |err| {
        if (err != error.PathAlreadyExists) return err;
    };

    // Generate stub for each module - comptime unrolled
    try generateModuleStub(allocator, io, "pcb", sexp.kicad.pcb, "PcbFile", output_dir);
    try generateModuleStub(allocator, io, "footprint", sexp.kicad.footprint, "FootprintFile", output_dir);
    try generateModuleStub(allocator, io, "netlist", sexp.kicad.netlist, "NetlistFile", output_dir);
    try generateModuleStub(allocator, io, "fp_lib_table", sexp.kicad.fp_lib_table, "FpLibTableFile", output_dir);
    try generateModuleStub(allocator, io, "symbol", sexp.kicad.symbol, "SymbolFile", output_dir);
    try generateModuleStub(allocator, io, "schematic", sexp.kicad.schematic, "SchematicFile", output_dir);

    try generateModuleStub(allocator, io, "footprint_v5", sexp.kicad.v5.footprint, "FootprintFile", output_dir);
    try generateModuleStub(allocator, io, "symbol_v6", sexp.kicad.v6.symbol, "SymbolFile", output_dir);
    // Add more modules as needed
}
