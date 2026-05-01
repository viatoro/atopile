/**
 * Hatch-stripe shaders for selection highlighting.
 *
 * The vertex shaders pass world-space position through so the fragment
 * shader can compute a stable diagonal stripe pattern in world coordinates.
 * The element geometry itself acts as the mask — stripes only appear where
 * triangles exist, so no stencil buffer is needed.
 */

import {
    GLSL_CAP_DISCARD, GLSL_LINESPACE_ARRAY, GLOW_BLINK_FREQ,
    EDGE_SMOOTHSTEP_START, EDGE_SMOOTHSTEP_END,
    GLOW_EDGE_START, GLOW_EDGE_END,
    GLOW_DURATION, GLOW_DISCARD_THRESHOLD,
} from "./shader_lib";

/** Vertex shader for polygon geometry (a_position + a_color) */
export const hatch_polygon_vert = `#version 300 es
uniform mat3 u_matrix;
in vec2 a_position;
in vec4 a_color;
out vec4 v_color;
out vec2 v_world;
void main() {
    v_color = a_color;
    v_world = a_position;
    gl_Position = vec4((u_matrix * vec3(a_position, 1)).xy, 0, 1);
}`;

/** Vertex shader for polyline geometry (a_position + a_color + a_cap_region) */
export const hatch_polyline_vert = `#version 300 es
uniform mat3 u_matrix;
in vec2 a_position;
in vec4 a_color;
in float a_cap_region;
out vec4 v_color;
out vec2 v_world;
out vec2 v_linespace;
out float v_cap_region;
${GLSL_LINESPACE_ARRAY}
void main() {
    int vi = int(gl_VertexID % 6);
    v_linespace = c_linespace[vi];
    v_cap_region = a_cap_region;
    v_color = a_color;
    v_world = a_position;
    gl_Position = vec4((u_matrix * vec3(a_position, 1)).xy, 0, 1);
}`;

/** Fragment shader for polygon hatch (no cap logic) */
export const hatch_polygon_frag = `#version 300 es
precision highp float;
uniform float u_depth;
uniform float u_alpha;
uniform float u_time;
uniform float u_spacing;
uniform float u_width;
in vec4 v_color;
in vec2 v_world;
out vec4 o_color;
void main() {
    float d = (v_world.x + v_world.y) / u_spacing - u_time;
    float t = fract(d);
    float aa = fwidth(d);
    float stripe = smoothstep(u_width - aa, u_width + aa, t);
    if (stripe < 0.5) discard;
    vec4 c = v_color;
    c.a *= u_alpha;
    o_color = c;
    gl_FragDepth = u_depth;
}`;

/** Fragment shader for polyline hatch (includes cap discard logic) */
export const hatch_polyline_frag = `#version 300 es
precision highp float;
uniform float u_depth;
uniform float u_alpha;
uniform float u_time;
uniform float u_spacing;
uniform float u_width;
in vec4 v_color;
in vec2 v_world;
in vec2 v_linespace;
in float v_cap_region;
out vec4 o_color;
void main() {
${GLSL_CAP_DISCARD}
    // Diagonal stripe pattern
    float d = (v_world.x + v_world.y) / u_spacing - u_time;
    float t = fract(d);
    float aa = fwidth(d);
    float stripe = smoothstep(u_width - aa, u_width + aa, t);
    // Thin outline at trace edges
    float edge = smoothstep(${EDGE_SMOOTHSTEP_START}, ${EDGE_SMOOTHSTEP_END}, abs(v_linespace.y));
    if (stripe < 0.5 && edge < 0.5) discard;
    vec4 c = v_color;
    c.a *= u_alpha;
    o_color = c;
    gl_FragDepth = u_depth;
}`;

/** Vertex shader for path-following polyline hatch (adds a_path_dist) */
export const hatch_pathline_vert = `#version 300 es
uniform mat3 u_matrix;
in vec2 a_position;
in vec4 a_color;
in float a_cap_region;
in float a_path_dist;
out vec4 v_color;
out vec2 v_world;
out vec2 v_linespace;
out float v_cap_region;
out float v_path_dist;
${GLSL_LINESPACE_ARRAY}
void main() {
    int vi = int(gl_VertexID % 6);
    v_linespace = c_linespace[vi];
    v_cap_region = a_cap_region;
    v_color = a_color;
    v_world = a_position;
    v_path_dist = a_path_dist;
    gl_Position = vec4((u_matrix * vec3(a_position, 1)).xy, 0, 1);
}`;

/** Fragment shader for path-following polyline hatch (stripes along trace) */
export const hatch_pathline_frag = `#version 300 es
precision highp float;
uniform float u_depth;
uniform float u_alpha;
uniform float u_time;
uniform float u_spacing;
uniform float u_width;
in vec4 v_color;
in vec2 v_world;
in vec2 v_linespace;
in float v_cap_region;
in float v_path_dist;
out vec4 o_color;
void main() {
${GLSL_CAP_DISCARD}
    // Path-following stripe pattern
    float d = v_path_dist / u_spacing - u_time;
    float t = fract(d);
    float aa = fwidth(d);
    float stripe = smoothstep(u_width - aa, u_width + aa, t);
    // Thin outline at trace edges
    float edge = smoothstep(${EDGE_SMOOTHSTEP_START}, ${EDGE_SMOOTHSTEP_END}, abs(v_linespace.y));
    if (stripe < 0.5 && edge < 0.5) discard;
    vec4 c = v_color;
    c.a *= u_alpha;
    o_color = c;
    gl_FragDepth = u_depth;
}`;

/** Fragment shader for polygon glow (3 blinks in 2 seconds) */
export const glow_polygon_frag = `#version 300 es
precision highp float;
uniform float u_time;
in vec4 v_color;
out vec4 o_color;
void main() {
    if (u_time > ${GLOW_DURATION}) discard;
    float intensity = sin(u_time * ${GLOW_BLINK_FREQ});
    if (intensity < ${GLOW_DISCARD_THRESHOLD}) discard;
    o_color = vec4(v_color.rgb, intensity);
}`;

/** Fragment shader for polyline glow (3 blinks in 2 seconds, soft edge) */
export const glow_polyline_frag = `#version 300 es
precision highp float;
uniform float u_time;
in vec4 v_color;
in vec2 v_linespace;
in float v_cap_region;
out vec4 o_color;
void main() {
${GLSL_CAP_DISCARD}
    float glow = smoothstep(${GLOW_EDGE_START}, ${GLOW_EDGE_END}, abs(v_linespace.y));
    if (u_time > ${GLOW_DURATION}) discard;
    float intensity = sin(u_time * ${GLOW_BLINK_FREQ}) * glow;
    if (intensity < ${GLOW_DISCARD_THRESHOLD}) discard;
    o_color = vec4(v_color.rgb, intensity);
}`;
