# Altium Binary PcbDoc Format: Binary Encoding

This document details the common serialization and encoding mechanisms found throughout `.PcbDoc` and `.PcbLib` files.

## Parameter Blocks

The majority of metadata, such as board configuration, rules, and component parameters, is stored in **Parameter Blocks**.

### Format
A parameter block is essentially a dictionary of key-value pairs formatted as a single string.
1. **Length Prefix**: The block usually starts with a 32-bit signed integer (`Int32`) specifying the size. Often, the size needs to be sanitized using a bitwise AND with `0x00FFFFFF` to remove any high-byte flags.
2. **String Data**: The block contains a sequence of key-value pairs separated by the pipe character (`|`). The string is typically null-terminated (C-string).
3. **Encoding**: Parameter blocks are primarily encoded in **Windows-1252** (not UTF-8).

### Example Parsing Logic
A standard parser would read the `Int32` size, read the exact number of bytes, find the null terminator, and then decode the string.
The resulting string looks like:
`|RECORD=Board|VERSION=6.0|METRIC=True|KEEPOUT=False`

Which translates into a dictionary:
- `RECORD` = `Board`
- `VERSION` = `6.0`
- `METRIC` = `True`
- `KEEPOUT` = `False`

## Pascal Short Strings

In some headers and specific internal structures, Altium uses Pascal short strings.
- **Length Prefix**: A single byte (`UInt8`) defining the string's length `N`.
- **String Data**: Exactly `N` characters follow.
- This is distinct from C-strings and does not usually require a null terminator.

## String Tables (`WideStrings6`)

To support Unicode (e.g., Cyrillic or Asian characters) without breaking backward compatibility of the Windows-1252 parameter blocks, Altium employs a string lookup mechanism.

The `WideStrings6` storage contains a `Data` stream which holds a parameter block.
- Keys are numbered sequentially: `ENCODEDTEXT0`, `ENCODEDTEXT1`, `ENCODEDTEXT2`, etc.
- The value for each key is a base64-like encoded version of the **UTF-16LE** string.

When a text primitive (`ATEXT6`) requires Unicode, it sets a property (like `WIDESTRING_INDEX`) pointing to the corresponding index in the `WideStrings6` dictionary.

## Binary Traversals and Skip/Exact Reads

Many primitive records contain fixed-length binary blocks (e.g., pads and vias).

### The `BinaryFormatReader` Pattern
A parser navigating these binary records must strictly follow the block sizes encoded in the stream.
When reading a primitive (like a Pad):
1. Read the block size.
2. Record the starting position.
3. Read the known fields sequentially (`Int32`, `Byte`, `Double`, etc.).
4. If the reader encounters new, unknown fields appended by newer versions of Altium, it uses the remaining block size to **skip** ahead.
`Remaining = BlockSize - (CurrentPosition - StartPosition)`

This exact traversal ensures that unknown fields in newer files do not desynchronize the sequential reading of the `Data` stream.
