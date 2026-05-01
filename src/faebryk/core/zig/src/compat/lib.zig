//! Zig version compatibility layer.
//!
//! This module provides API compatibility for breaking changes between Zig versions,
//! allowing incremental migration without large-scale refactoring.
//!
//! Current compat shims:
//! - DoublyLinkedList: Generic linked list (Zig 0.15 de-generified std.DoublyLinkedList)
//! - MonoTimer: Monotonic clock timer (Zig 0.16 removed std.time.Timer)

pub const linked_list = @import("linked_list.zig");
pub const DoublyLinkedList = linked_list.DoublyLinkedList;

pub const timer = @import("timer.zig");
pub const MonoTimer = timer.MonoTimer;
