/**
 * Shared GLSL snippets injected into shaders via template literals.
 * Avoids copy-pasting the same logic across multiple shader strings.
 */

/** Round-cap discard logic for polyline fragment shaders.
 *  Requires: `in vec2 v_linespace; in float v_cap_region;` */
export const GLSL_CAP_DISCARD = `
    float x = v_linespace.x;
    float y = v_linespace.y;
    if (x < (-1.0 + v_cap_region)) {
        float a = (1.0 + x) / v_cap_region;
        x = mix(-1.0, 0.0, a);
        if (x*x + y*y >= 1.0) discard;
    } else if (x > (1.0 - v_cap_region)) {
        float a = (x - (1.0 - v_cap_region)) / v_cap_region;
        x = mix(0.0, 1.0, a);
        if (x*x + y*y >= 1.0) discard;
    }`;

/** Linespace coordinate array for polyline vertex shaders (2 triangles = 6 verts). */
export const GLSL_LINESPACE_ARRAY = `vec2 c_linespace[6] = vec2[](
    vec2(-1, -1), vec2( 1, -1), vec2(-1,  1),
    vec2(-1,  1), vec2( 1, -1), vec2( 1,  1)
);`;

/** 3π/2: produces 3 blinks over 2 seconds when used as sin(u_time * FREQ). */
export const GLOW_BLINK_FREQ = "4.71239";

/** Edge smoothstep range for trace outlines. */
export const EDGE_SMOOTHSTEP_START = "0.7";
export const EDGE_SMOOTHSTEP_END = "1.0";

/** Glow edge softness range. */
export const GLOW_EDGE_START = "1.0";
export const GLOW_EDGE_END = "0.3";

/** Glow duration in seconds. */
export const GLOW_DURATION = "2.0";

/** Glow discard threshold. */
export const GLOW_DISCARD_THRESHOLD = "0.01";
