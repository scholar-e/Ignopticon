/**
 * gpu - draws the color overlays and the map tile underlay with WebGL, so both follow every frame of a drag or zoom.
 *
 * An overlay's colors are baked once per grid into an equirectangular texture. A fragment shader then inverts
 * the projection for each screen pixel and samples that texture, replacing the per-pixel CPU interpolation that
 * otherwise has to run after every move. Only the orthographic projection is supported; callers fall back to the
 * CPU path for the others.
 */
var gpu = function() {
    "use strict";

    var TEXTURE_WIDTH = 1024;   // global grids: a power of two so the texture can wrap in longitude (WebGL 1 rule)
    var TEXTURE_HEIGHT = 512;   // 0.35° texels: finer than the 0.5° GFS grid, and a quick bake on slow machines
    var MAX_REGIONAL_SIZE = 4096;  // regional grids get a texel per grid point, up to this many per side
    var SUPPORTED = d3.set(["orthographic"]);

    var VERTEX_SHADER = [
        "attribute vec2 a_position;",
        "void main() { gl_Position = vec4(a_position, 0.0, 1.0); }"
    ].join("\n");

    // Port of d3 v3's orthographic projection.invert followed by its rotation.invert (d3_geo_rotationφγ, then λ).
    var FRAGMENT_SHADER = [
        "precision highp float;",
        "uniform sampler2D u_colors;",
        "uniform float u_height;",      // canvas height, to flip gl_FragCoord into screen coordinates
        "uniform float u_scale;",
        "uniform vec2 u_translate;",
        "uniform vec3 u_rotate;",       // [δλ, δφ, δγ] in radians
        "uniform vec4 u_box;",          // texture extent in degrees: [west, north, width, height]
        "uniform float u_regional;",    // 1 if the texture covers only part of the globe
        "const float PI = 3.141592653589793;",
        "void main() {",
        "    vec2 p = vec2(gl_FragCoord.x, u_height - gl_FragCoord.y);",
        "    float X = (p.x - u_translate.x) / u_scale;",
        "    float Y = (u_translate.y - p.y) / u_scale;",
        "    float ρ2 = X * X + Y * Y;",
        "    if (ρ2 > 1.0) discard;",
        "    float λr = atan(X, sqrt(1.0 - ρ2));",
        "    float φr = asin(clamp(Y, -1.0, 1.0));",
        "    float cosφ = cos(φr);",
        "    float x = cos(λr) * cosφ, y = sin(λr) * cosφ, z = sin(φr);",
        "    float cδφ = cos(u_rotate.y), sδφ = sin(u_rotate.y), cδγ = cos(u_rotate.z), sδγ = sin(u_rotate.z);",
        "    float k = z * cδγ - y * sδγ;",
        "    float λ = atan(y * cδγ + z * sδγ, x * cδφ + k * sδφ) - u_rotate.x;",
        "    float φ = asin(clamp(k * cδφ - x * sδφ, -1.0, 1.0));",
        "    vec2 uv = vec2(mod(degrees(λ) - u_box.x, 360.0) / u_box.z, (u_box.y - degrees(φ)) / u_box.w);",
        "    if (u_regional > 0.5 && (uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0)) discard;",
        "    gl_FragColor = texture2D(u_colors, uv);",
        "}"
    ].join("\n").replace(/λ/g, "lam").replace(/φ/g, "phi").replace(/ρ/g, "rho").replace(/δ/g, "d").replace(/γ/g, "g");

    function compile(gl, type, source) {
        var shader = gl.createShader(type);
        gl.shaderSource(shader, source);
        gl.compileShader(shader);
        if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
            throw new Error(gl.getShaderInfoLog(shader));
        }
        return shader;
    }

    /**
     * @returns {Object} the texture's extent and size: the whole globe for global grids, otherwise the grid's own box
     *          with a texel centered on each grid point.
     */
    function textureLayout(grid) {
        var g = grid.geometry;
        if (!g || g.continuous) {
            return {west: -180, north: 90, width: 360, height: 180, nx: TEXTURE_WIDTH, ny: TEXTURE_HEIGHT};
        }
        return {
            west: g.lo1 - g.dx / 2,
            north: g.la1 + g.dy / 2,
            width: g.nx * g.dx,
            height: g.ny * g.dy,
            nx: Math.min(g.nx, MAX_REGIONAL_SIZE),
            ny: Math.min(g.ny, MAX_REGIONAL_SIZE),
            regional: true
        };
    }

    /**
     * Fills an RGBA texture with the overlay color at each texel center, premultiplied by alpha so that linear
     * filtering blends correctly into areas where the grid is undefined.
     */
    function bakeColors(grid, layout, alpha) {
        var nx = layout.nx, ny = layout.ny, pixels = new Uint8Array(nx * ny * 4);
        var isVector = grid.field === "vector", scale = grid.scale;
        for (var j = 0, p = 0; j < ny; j++) {
            var φ = layout.north - (j + 0.5) * layout.height / ny;
            for (var i = 0; i < nx; i++, p += 4) {
                var λ = layout.west + (i + 0.5) * layout.width / nx;
                var value = grid.interpolate(λ, φ);
                var scalar = isVector ? value && value[2] : value;
                if (µ.isValue(scalar)) {
                    var rgba = scale.gradient(scalar, alpha), a = rgba[3] / 255;  // some scales vary alpha
                    pixels[p    ] = rgba[0] * a;
                    pixels[p + 1] = rgba[1] * a;
                    pixels[p + 2] = rgba[2] * a;
                    pixels[p + 3] = rgba[3];
                }
            }
        }
        return pixels;
    }

    /**
     * @param canvas the canvas to draw into.
     * @param alpha overlay opacity on the scale [0, 255].
     * @returns {Object} an overlay renderer, or null if WebGL is unavailable.
     */
    function createOverlayRenderer(canvas, alpha) {
        var gl = canvas.getContext("webgl", {premultipliedAlpha: true});
        if (!gl) return null;

        var program = gl.createProgram();
        gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER));
        gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, FRAGMENT_SHADER));
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            throw new Error(gl.getProgramInfoLog(program));
        }
        gl.useProgram(program);

        // One quad covering the canvas; the fragment shader does all the work.
        gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
        var position = gl.getAttribLocation(program, "a_position");
        gl.enableVertexAttribArray(position);
        gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);

        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);  // baked colors are premultiplied

        var uniforms = {};
        ["u_colors", "u_height", "u_scale", "u_translate", "u_rotate", "u_box", "u_regional"].forEach(function(name) {
            uniforms[name] = gl.getUniformLocation(program, name);
        });
        gl.uniform1i(uniforms.u_colors, 0);

        var layers = [];  // [{grid, texture, layout}] in draw order

        function bake(grid) {
            var layout = textureLayout(grid), pixels = bakeColors(grid, layout, alpha), texture = gl.createTexture();
            gl.bindTexture(gl.TEXTURE_2D, texture);
            // Regional textures need not be a power of two, and WebGL 1 then allows only CLAMP_TO_EDGE.
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, layout.regional ? gl.CLAMP_TO_EDGE : gl.REPEAT);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
            gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
            gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, layout.nx, layout.ny, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
            return {grid: grid, texture: texture, layout: layout};
        }

        return {
            /**
             * @returns {Boolean} true if the overlay for this projection can be drawn on the GPU.
             */
            supports: function(projectionName) {
                return SUPPORTED.has(projectionName);
            },

            /**
             * Sets the overlays to draw, bottom to top. Grids already uploaded keep their textures; the colors of
             * new ones are baked and uploaded, and textures of grids no longer listed are freed.
             */
            setGrids: function(grids) {
                var next = grids.map(function(grid) {
                    return _.find(layers, function(layer) { return layer.grid === grid; }) || bake(grid);
                });
                layers.forEach(function(layer) {
                    if (!_.contains(next, layer)) gl.deleteTexture(layer.texture);
                });
                layers = next;
            },

            draw: function(projection) {
                var rotate = projection.rotate(), translate = projection.translate(), rad = Math.PI / 180;
                gl.viewport(0, 0, canvas.width, canvas.height);
                gl.clearColor(0, 0, 0, 0);
                gl.clear(gl.COLOR_BUFFER_BIT);
                gl.uniform1f(uniforms.u_height, canvas.height);
                gl.uniform1f(uniforms.u_scale, projection.scale());
                gl.uniform2f(uniforms.u_translate, translate[0], translate[1]);
                gl.uniform3f(uniforms.u_rotate, rotate[0] * rad, rotate[1] * rad, (rotate[2] || 0) * rad);
                layers.forEach(function(layer) {
                    var layout = layer.layout;
                    gl.bindTexture(gl.TEXTURE_2D, layer.texture);
                    gl.uniform4f(uniforms.u_box, layout.west, layout.north, layout.width, layout.height);
                    gl.uniform1f(uniforms.u_regional, layout.regional ? 1 : 0);
                    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
                });
            },

            clear: function() {
                gl.clearColor(0, 0, 0, 0);
                gl.clear(gl.COLOR_BUFFER_BIT);
            }
        };
    }

    var TILE_SIZE = 256;          // pixels per side of a Web Mercator tile
    var TILE_MESH = 16;           // mesh subdivisions per tile side, so tiles curve with the globe
    var MAX_TILE_TEXTURES = 400;  // textures kept on the GPU before the least recently used are dropped, though
                                  // never those of the frame being drawn
    var MAX_TILE_LOADS = 6;       // tile requests in flight at once
    var TILE_SPLIT_SIZE = TILE_SIZE * Math.SQRT2;  // a tile drawn larger than this is replaced by its four children
    var TILE_SAMPLES = 4;         // sample points per tile side, to find where a tile lands on the screen
    var SCREEN_PROBES = 8;        // probe points per screen side, to find tiles larger than the screen
    var COARSE_FIRST_LEVELS = 3;  // tiles this many zooms coarser are requested first, so a layer covers the screen
                                  // in a few requests before its detail arrives
    var MAX_FALLBACK_LEVELS = 8;  // how many zoom levels up to look for a loaded tile to stand in for a missing one

    // Forward d3 v3 rotation (λ, then φγ) followed by the orthographic projection, per vertex.
    var TILE_VERTEX_SHADER = [
        "attribute vec2 a_lonlat;",     // degrees
        "attribute vec2 a_uv;",
        "uniform vec2 u_size;",         // canvas size
        "uniform float u_scale;",
        "uniform vec2 u_translate;",
        "uniform vec3 u_rotate;",       // [δλ, δφ, δγ] in radians
        "uniform vec4 u_uvRect;",       // part of the texture to use, [u0, v0, du, dv]: all of it, or part of an ancestor
        "varying vec2 v_uv;",
        "varying float v_front;",       // > 0 on the hemisphere facing the viewer
        "varying vec2 v_lonlat;",
        "void main() {",
        "    v_lonlat = a_lonlat;",
        "    float λ = radians(a_lonlat.x) + u_rotate.x, φ = radians(a_lonlat.y);",
        "    float cosφ = cos(φ);",
        "    float x = cos(λ) * cosφ, y = sin(λ) * cosφ, z = sin(φ);",
        "    float cδφ = cos(u_rotate.y), sδφ = sin(u_rotate.y), cδγ = cos(u_rotate.z), sδγ = sin(u_rotate.z);",
        "    float k = z * cδφ + x * sδφ;",
        "    float λr = atan(y * cδγ - k * sδγ, x * cδφ - z * sδφ);",
        "    float φr = asin(clamp(k * cδγ + y * sδγ, -1.0, 1.0));",
        "    float cosφr = cos(φr);",
        "    v_front = cosφr * cos(λr);",
        "    vec2 p = u_translate + u_scale * vec2(cosφr * sin(λr), -sin(φr));",
        "    gl_Position = vec4(p.x / u_size.x * 2.0 - 1.0, 1.0 - p.y / u_size.y * 2.0, 0.0, 1.0);",
        "    v_uv = u_uvRect.xy + a_uv * u_uvRect.zw;",
        "}"
    ].join("\n").replace(/λ/g, "lam").replace(/φ/g, "phi").replace(/δ/g, "d").replace(/γ/g, "g");

    var TILE_FRAGMENT_SHADER = [
        "precision mediump float;",
        "uniform sampler2D u_tile;",
        "uniform vec4 u_clip;",         // the region drawn, [west, south, east, north] in degrees
        "varying vec2 v_uv;",
        "varying float v_front;",
        "varying vec2 v_lonlat;",
        "void main() {",
        "    if (v_front < 0.0) discard;",
        "    if (any(lessThan(v_lonlat, u_clip.xy)) || any(greaterThan(v_lonlat, u_clip.zw))) discard;",
        "    gl_FragColor = texture2D(u_tile, v_uv);",
        "}"
    ].join("\n");

    function tileLon(x, n) {
        return x / n * 360 - 180;
    }

    function tileLat(y, n) {
        return Math.atan(Math.sinh(Math.PI * (1 - 2 * y / n))) * 180 / Math.PI;
    }

    /**
     * @param canvas the canvas to draw into.
     * @param sources map of underlay name to {url: "/tiles/name/{z}/{x}/{y}", or an array of layers drawn bottom to
     *        top, each such a URL or {url:, maxZoom: of that layer}, minZoom:, maxZoom:, base: zoom of a
     *        whole-world layer drawn first (optional), bounds: [west, south, east, north] (optional),
     *        nearest: true for categorical rasters that must not be blended between pixels}. URLs may also hold
     *        {name} placeholders filled in from setParams.
     * @returns {Object} a tile renderer, or null if WebGL is unavailable.
     */
    function createTileRenderer(canvas, sources) {
        var gl = canvas.getContext("webgl", {premultipliedAlpha: true});
        if (!gl) return null;

        var program = gl.createProgram();
        gl.attachShader(program, compile(gl, gl.VERTEX_SHADER, TILE_VERTEX_SHADER));
        gl.attachShader(program, compile(gl, gl.FRAGMENT_SHADER, TILE_FRAGMENT_SHADER));
        gl.linkProgram(program);
        if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
            throw new Error(gl.getProgramInfoLog(program));
        }
        gl.useProgram(program);
        gl.enable(gl.BLEND);
        gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);  // textures are uploaded premultiplied
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);

        var uniforms = {};
        ["u_size", "u_scale", "u_translate", "u_rotate", "u_uvRect", "u_tile", "u_clip"].forEach(function(name) {
            uniforms[name] = gl.getUniformLocation(program, name);
        });
        var lonlat = gl.getAttribLocation(program, "a_lonlat"), uv = gl.getAttribLocation(program, "a_uv");
        gl.enableVertexAttribArray(lonlat);
        gl.enableVertexAttribArray(uv);
        gl.uniform1i(uniforms.u_tile, 0);

        // Every tile mesh shares one index buffer: TILE_MESH x TILE_MESH quads of two triangles.
        var indices = [], m = TILE_MESH + 1;
        for (var j = 0; j < TILE_MESH; j++) {
            for (var i = 0; i < TILE_MESH; i++) {
                var a = j * m + i;
                indices.push(a, a + 1, a + m, a + 1, a + m + 1, a + m);
            }
        }
        gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, gl.createBuffer());
        gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array(indices), gl.STATIC_DRAW);

        var meshes = {};    // "z/x/y" -> vertex buffer of [lon, lat, u, v]
        var textures = {};  // "source/z/x/y" -> {texture, used} once loaded, or {missing: true}
        var loading = {};   // "source/z/x/y" -> {image, wanted: last frame that drew the tile} while requested
        var inFlight = 0, frame = 0, source = null, sourceName = null, lastProjection = null, redrawPending = false;
        var layer = 0;  // index of the source's layer being drawn
        var params = {}, layerList = [], layerUrls = [];  // the source's layers, and their URLs with params filled in
        var WORLD = [-180, -90, 180, 90], clip = WORLD;   // the region drawn, see setBounds

        function mesh(z, x, y) {
            var key = z + "/" + x + "/" + y;
            if (meshes[key]) return meshes[key];
            var n = Math.pow(2, z), data = [];
            for (var j = 0; j <= TILE_MESH; j++) {
                var lat = tileLat(y + j / TILE_MESH, n);
                for (var i = 0; i <= TILE_MESH; i++) {
                    data.push(tileLon(x + i / TILE_MESH, n), lat, i / TILE_MESH, j / TILE_MESH);
                }
            }
            var buffer = gl.createBuffer();
            gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
            gl.bufferData(gl.ARRAY_BUFFER, new Float32Array(data), gl.STATIC_DRAW);
            return meshes[key] = buffer;
        }

        function resolveLayers() {
            layerList = source ? [].concat(source.url).map(function(l) { return _.isString(l) ? {url: l} : l; }) : [];
            layerUrls = layerList.map(function(l) {
                // Leaves {z}, {x} and {y} for request to fill in.
                return l.url.replace(/\{(\w+)\}/g, function(match, name) {
                    return name in params ? params[name] : match;
                });
            });
        }

        function tileKey(z, x, y) {
            return layerUrls[layer] + "/" + z + "/" + x + "/" + y;
        }

        function inBounds(z, x, y) {
            var n = Math.pow(2, z), west = tileLon(x, n), east = tileLon(x + 1, n), north = tileLat(y, n), south = tileLat(y + 1, n);
            return [source.bounds, clip].every(function(b) {
                return !b || !(east < b[0] || west > b[2] || north < b[1] || south > b[3]);
            });
        }

        function request(z, x, y) {
            var key = tileKey(z, x, y), name = sourceName, url = layerUrls[layer];
            if (loading[key]) loading[key].wanted = frame;
            if (textures[key] || loading[key] || inFlight >= MAX_TILE_LOADS) return;
            var image = new Image();
            loading[key] = {image: image, wanted: frame};
            inFlight++;
            image.onload = function() {
                var texture = gl.createTexture(), filter = sources[name].nearest ? gl.NEAREST : gl.LINEAR;
                gl.bindTexture(gl.TEXTURE_2D, texture);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
                gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
                gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, image);
                textures[key] = {texture: texture, used: frame};
                done();
            };
            image.onerror = function() {
                textures[key] = {missing: true};  // e.g. beyond the source's zoom range; ancestors stand in
                done();
            };
            function done() {
                delete loading[key];
                inFlight--;
                evict();
                scheduleRedraw();
            }
            image.src = url.replace("{z}", z).replace("{x}", x).replace("{y}", y);
        }

        /**
         * Cancels requests for tiles the last frame did not draw, e.g. of a zoom level the view has since left, so
         * they do not hold up the tiles now on the screen.
         */
        function abandonStaleRequests() {
            var stale = _.filter(_.keys(loading), function(key) { return loading[key].wanted < frame; });
            stale.forEach(function(key) {
                var image = loading[key].image;
                image.onload = image.onerror = null;
                image.src = "";  // aborts the download
                delete loading[key];
                inFlight--;
            });
            if (stale.length > 0) scheduleRedraw();
        }

        function evict() {
            var loaded = _.filter(_.keys(textures), function(k) { return textures[k].texture; });
            if (loaded.length <= MAX_TILE_TEXTURES) return;
            // Dropping a tile still on the screen would only load it again, and drop another, round and round.
            var old = _.filter(loaded, function(k) { return textures[k].used < frame; });
            _.sortBy(old, function(k) { return textures[k].used; })
                .slice(0, loaded.length - MAX_TILE_TEXTURES)
                .forEach(function(k) {
                    gl.deleteTexture(textures[k].texture);
                    delete textures[k];
                });
        }

        function scheduleRedraw() {
            if (redrawPending || !lastProjection) return;
            redrawPending = true;
            requestAnimationFrame(function() {
                redrawPending = false;
                if (lastProjection && source) draw(lastProjection);
            });
        }

        /**
         * @returns {Function} the inverse of the orthographic projection as f(x, y) -> [λ, φ] in degrees, or null off
         *          the globe. The same math as the overlay's fragment shader; much cheaper per point than d3's
         *          general inverse, which matters as this runs for hundreds of points on every frame of a drag.
         */
        function orthographicInverse(projection) {
            var t = projection.translate(), s = projection.scale(), rotate = projection.rotate(), rad = Math.PI / 180;
            var δλ = rotate[0] * rad, cδφ = Math.cos(rotate[1] * rad), sδφ = Math.sin(rotate[1] * rad);
            var cδγ = Math.cos((rotate[2] || 0) * rad), sδγ = Math.sin((rotate[2] || 0) * rad);
            return function(px, py) {
                var X = (px - t[0]) / s, Y = (t[1] - py) / s, ρ2 = X * X + Y * Y;
                if (ρ2 > 1) return null;
                var λr = Math.atan2(X, Math.sqrt(1 - ρ2)), φr = Math.asin(Y), cosφ = Math.cos(φr);
                var x = Math.cos(λr) * cosφ, y = Math.sin(λr) * cosφ, z = Math.sin(φr), k = z * cδγ - y * sδγ;
                var λ = Math.atan2(y * cδγ + z * sδγ, x * cδφ + k * sδφ) - δλ;
                var φ = Math.asin(µ.clamp(k * cδφ - x * sδφ, -1, 1));
                return [µ.floorMod(λ / rad + 180, 360) - 180, φ / rad];
            };
        }

        /**
         * @returns {Function} the orthographic projection as f(λ, φ) in degrees -> [x, y, front], where front > 0 on
         *          the hemisphere facing the viewer. The same math as the vertex shader.
         */
        function orthographicForward(projection) {
            var t = projection.translate(), s = projection.scale(), rotate = projection.rotate(), rad = Math.PI / 180;
            var δλ = rotate[0] * rad, cδφ = Math.cos(rotate[1] * rad), sδφ = Math.sin(rotate[1] * rad);
            var cδγ = Math.cos((rotate[2] || 0) * rad), sδγ = Math.sin((rotate[2] || 0) * rad);
            return function(λ, φ) {
                var λr = λ * rad + δλ, φr = φ * rad, cosφ = Math.cos(φr);
                var x = Math.cos(λr) * cosφ, y = Math.sin(λr) * cosφ, z = Math.sin(φr), k = z * cδφ + x * sδφ;
                return [t[0] + s * (y * cδγ - k * sδγ), t[1] - s * (k * cδγ + y * sδγ), x * cδφ - z * sδφ];
            };
        }

        /**
         * @returns {Array} the [z, x, y] tiles covering the visible part of the globe, nearest the center first, each
         *          at the zoom that draws it about TILE_SIZE pixels wide: fine at the center of the globe, coarse
         *          toward the limb, where tiles are seen edge on. Found by splitting tiles from zoom 0 down.
         */
        function visibleTiles(projection, minZoom, maxZoom) {
            var forward = orthographicForward(projection), invert = orthographicInverse(projection);
            var w = canvas.width, h = canvas.height, t = projection.translate(), tiles = [];
            // Points of the screen, so a tile that holds the whole screen (or a big part, between its samples) is
            // still found.
            var probes = [];
            for (var j = 0; j <= SCREEN_PROBES; j++) {
                for (var i = 0; i <= SCREEN_PROBES; i++) {
                    var c = invert(w * i / SCREEN_PROBES, h * j / SCREEN_PROBES);
                    if (c) probes.push(c);
                }
            }
            var m = TILE_SAMPLES, points = new Array((m + 1) * (m + 1));

            function visit(z, x, y) {
                if (!inBounds(z, x, y)) return;
                var n = Math.pow(2, z), west = tileLon(x, n), east = tileLon(x + 1, n);
                var north = tileLat(y, n), south = tileLat(y + 1, n), visible = false, size = 0;
                for (var j = 0; j <= m; j++) {
                    var φ = tileLat(y + j / m, n);
                    for (var i = 0; i <= m; i++) {
                        var p = points[j * (m + 1) + i] = forward(tileLon(x + i / m, n), φ);
                        visible = visible || (p[2] > 0 && p[0] >= 0 && p[0] <= w && p[1] >= 0 && p[1] <= h);
                    }
                }
                for (var k = 0; !visible && k < probes.length; k++) {
                    var q = probes[k];
                    visible = q[0] >= west && q[0] <= east && q[1] >= south && q[1] <= north;
                }
                if (!visible) return;
                // The tile's size on the screen where it is drawn largest, from the gaps between front-facing samples.
                for (j = 0; j <= m; j++) {
                    for (i = 0; i <= m; i++) {
                        var a = points[j * (m + 1) + i];
                        if (a[2] <= 0) continue;
                        var right = i < m && points[j * (m + 1) + i + 1], below = j < m && points[(j + 1) * (m + 1) + i];
                        if (right && right[2] > 0) size = Math.max(size, m * Math.hypot(right[0] - a[0], right[1] - a[1]));
                        if (below && below[2] > 0) size = Math.max(size, m * Math.hypot(below[0] - a[0], below[1] - a[1]));
                    }
                }
                if (z < minZoom || (z < maxZoom && size > TILE_SPLIT_SIZE)) {
                    visit(z + 1, 2 * x, 2 * y);
                    visit(z + 1, 2 * x + 1, 2 * y);
                    visit(z + 1, 2 * x, 2 * y + 1);
                    visit(z + 1, 2 * x + 1, 2 * y + 1);
                    return;
                }
                var center = forward(tileLon(x + 0.5, n), tileLat(y + 0.5, n));
                var dx = center[0] - t[0], dy = center[1] - t[1];
                tiles.push([z, x, y, dx * dx + dy * dy]);
            }

            visit(0, 0, 0);
            return _.sortBy(tiles, function(tile) { return tile[3]; });
        }

        function drawTile(z, x, y, entry, uvRect) {
            gl.bindBuffer(gl.ARRAY_BUFFER, mesh(z, x, y));
            gl.vertexAttribPointer(lonlat, 2, gl.FLOAT, false, 16, 0);
            gl.vertexAttribPointer(uv, 2, gl.FLOAT, false, 16, 8);
            gl.bindTexture(gl.TEXTURE_2D, entry.texture);
            gl.uniform4fv(uniforms.u_uvRect, uvRect);
            gl.drawElements(gl.TRIANGLES, indices.length, gl.UNSIGNED_SHORT, 0);
            entry.used = frame;
        }

        /**
         * Draws the tile, or failing that its loaded children (left over from a closer zoom) and, where those are
         * missing, the part of its nearest loaded ancestor that covers it.
         */
        function drawTileOrStandIn(z, x, y) {
            var entry = textures[tileKey(z, x, y)];
            if (entry && entry.texture) return drawTile(z, x, y, entry, [0, 0, 1, 1]);
            var children = [], any = false;
            for (var j = 0; j < 2; j++) {
                for (var i = 0; i < 2; i++) {
                    var child = textures[tileKey(z + 1, 2 * x + i, 2 * y + j)];
                    children.push(child && child.texture ? child : null);
                    any = any || !!(child && child.texture);
                }
            }
            if (!any) return drawAncestor(z, x, y, 1);
            children.forEach(function(child, k) {
                var cx = 2 * x + k % 2, cy = 2 * y + (k >> 1);
                // Quarter by quarter, so a transparent layer is not drawn twice anywhere.
                if (child) drawTile(z + 1, cx, cy, child, [0, 0, 1, 1]); else drawAncestor(z + 1, cx, cy, 2);
            });
        }

        /**
         * Draws the part of the tile's nearest loaded ancestor, at least `from` levels up, that covers it.
         */
        function drawAncestor(z, x, y, from) {
            for (var d = from; d <= MAX_FALLBACK_LEVELS && d <= z; d++) {
                var f = Math.pow(2, d), ax = Math.floor(x / f), ay = Math.floor(y / f);
                var entry = textures[tileKey(z - d, ax, ay)];
                if (entry && entry.texture) {
                    drawTile(z, x, y, entry, [(x - ax * f) / f, (y - ay * f) / f, 1 / f, 1 / f]);
                    return;
                }
            }
        }

        function draw(projection) {
            lastProjection = projection;
            frame++;
            gl.viewport(0, 0, canvas.width, canvas.height);
            gl.clearColor(0, 0, 0, 0);
            gl.clear(gl.COLOR_BUFFER_BIT);
            if (!source) return;

            var rotate = projection.rotate(), translate = projection.translate(), rad = Math.PI / 180;
            gl.uniform2f(uniforms.u_size, canvas.width, canvas.height);
            gl.uniform1f(uniforms.u_scale, projection.scale());
            gl.uniform2f(uniforms.u_translate, translate[0], translate[1]);
            gl.uniform3f(uniforms.u_rotate, rotate[0] * rad, rotate[1] * rad, (rotate[2] || 0) * rad);
            gl.uniform4f(uniforms.u_clip, clip[0], clip[1], clip[2], clip[3]);

            var tilesAt = {};  // by maximum zoom, as layers may stop at different zooms
            for (layer = 0; layer < layerList.length; layer++) {
                var maxZoom = Math.min(source.maxZoom, layerList[layer].maxZoom || source.maxZoom);
                var tiles = tilesAt[maxZoom] || (tilesAt[maxZoom] = visibleTiles(projection, source.minZoom, maxZoom));
                // A coarse whole-world layer goes down first, filling any gaps between sampled tiles near the limb.
                // Only under the bottom layer: through a transparent layer above, it would show as blurry blobs.
                if (_.isNumber(source.base) && layer === 0) {
                    var n = Math.pow(2, source.base);
                    for (var by = 0; by < n; by++) {
                        for (var bx = 0; bx < n; bx++) {
                            if (!inBounds(source.base, bx, by)) continue;
                            var entry = textures[tileKey(source.base, bx, by)];
                            if (entry && entry.texture) drawTile(source.base, bx, by, entry, [0, 0, 1, 1]);
                            else request(source.base, bx, by);
                        }
                    }
                }
                var coarse = {};
                tiles.forEach(function(tile) {
                    var d = Math.min(COARSE_FIRST_LEVELS, tile[0] - source.minZoom);
                    if (d <= 0) return;
                    var f = Math.pow(2, d), key = (tile[0] - d) + "/" + Math.floor(tile[1] / f) + "/" + Math.floor(tile[2] / f);
                    if (!coarse[key] && !textures[tileKey(tile[0] - d, Math.floor(tile[1] / f), Math.floor(tile[2] / f))]) {
                        coarse[key] = true;
                        request(tile[0] - d, Math.floor(tile[1] / f), Math.floor(tile[2] / f));
                    }
                });
                tiles.forEach(function(tile) {
                    if (!textures[tileKey(tile[0], tile[1], tile[2])]) request(tile[0], tile[1], tile[2]);
                    drawTileOrStandIn(tile[0], tile[1], tile[2]);
                });
            }
            layer = 0;
            abandonStaleRequests();
        }

        return {
            /**
             * @returns {Boolean} true if tiles can be drawn in this projection.
             */
            supports: function(projectionName) {
                return SUPPORTED.has(projectionName);
            },

            /**
             * Selects the underlay by name, or none if the name is unknown.
             */
            setSource: function(name) {
                if ((sources[name] ? name : null) === sourceName) return;
                sourceName = sources[name] ? name : null;
                source = sourceName ? sources[name] : null;
                resolveLayers();
            },

            /**
             * Sets values for {name} placeholders in the sources' URLs, e.g. {date: "2026-10-29"}.
             */
            setParams: function(values) {
                if (_.every(values, function(v, k) { return params[k] === v; })) return;
                _.extend(params, values);
                resolveLayers();
            },

            /**
             * Limits every source to the region [west, south, east, north] (not across the antimeridian): tiles
             * outside it are not loaded, and those across its edge are cut at it. Null for the whole world.
             */
            setBounds: function(bounds) {
                clip = bounds || WORLD;
            },

            draw: draw,

            clear: function() {
                lastProjection = null;
                gl.clearColor(0, 0, 0, 0);
                gl.clear(gl.COLOR_BUFFER_BIT);
            }
        };
    }

    return {
        createOverlayRenderer: createOverlayRenderer,
        createTileRenderer: createTileRenderer
    };

}();
