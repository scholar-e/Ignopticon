/**
 * earth - a project to visualize global air data.
 *
 * Copyright (c) 2014 Cameron Beccario
 * The MIT License - http://opensource.org/licenses/MIT
 *
 * https://github.com/cambecc/earth
 */
(function() {
    "use strict";

    var SECOND = 1000;
    var MINUTE = 60 * SECOND;
    var HOUR = 60 * MINUTE;
    var MAX_TASK_TIME = 100;                  // amount of time before a task yields control (millis)
    var MIN_SLEEP_TIME = 25;                  // amount of time a task waits before resuming (millis)
    var MIN_MOVE = 4;                         // slack before a drag operation beings (pixels)
    var MOVE_END_WAIT = 200;                  // time to wait for a move operation to be considered done (millis)

    var OVERLAY_ALPHA = Math.floor(0.4*255);  // overlay transparency (on scale [0, 255])
    var INTENSITY_SCALE_STEP = 10;            // step size of particle intensity color scale
    var MAX_PARTICLE_AGE = 100;               // max number of frames a particle is drawn before regeneration
    var PARTICLE_LINE_WIDTH = 1.0;            // line width of a drawn particle
    var PARTICLE_MULTIPLIER = 7;              // particle count scalar (completely arbitrary--this values looks nice)
    var PARTICLE_DENSITY = {off: 0, few: 0.25, some: 0.5, many: 1};  // menu choices, as shares of PARTICLE_MULTIPLIER
    var AIR_OVERLAYS = [                      // overlays that can be switched on together in air mode, in draw order
        "wind", "temp", "relative_humidity", "air_density", "wind_power_density", "total_precipitable_water",
        "total_cloud_water", "mean_sea_level_pressure", "fire_heat"
    ];
    var MENU_SCREEN_SHARE = 0.2;              // the menus' controls cover at most this share of the screen at 100% zoom
    var MENU_FONT_RANGE = [6, 20];            // limits on the root font size the menu is sized by (CSS pixels)
    var MENU_WIDTH_SHARE = 0.9;               // the two menus side by side take at most this share of the width
    var PARTICLE_REDUCTION = 0.75;            // reduce particle count to this much of normal for mobile devices
    var FRAME_RATE = 40;                      // desired milliseconds per frame
    var PARTICLE_FRAME_BUDGET = 0.5;          // share of FRAME_RATE particles may use before the count is reduced
    var PARTICLE_ADAPT_FRAMES = 25;           // frames averaged before deciding whether to reduce the count
    var MIN_PARTICLE_COUNT = 1000;            // never reduce the particle count below this
    var MAX_PARTICLE_STEP = 100;              // longer steps cross a cut in the projection, so are not drawn (pixels)
    var SMOKE_WIDTH = [2, 16];                // smoke stroke width leaving the fire and at the end of its life (pixels)
    var SMOKE_ALPHA = [0.35, 0.08];           // and its opacity: the strokes overlap into a cloud that thins downwind
    var SMOKE_AGE_FACTOR = 3;                 // smoke particles live this many times longer, so plumes reach downwind
    var SMOKE_EMITTER_REFRESH = 10;           // while the view moves, frames between finding the hotspots in view
    var PARTICLE_SPEED_SCALE_LIMIT = 3000;    // beyond this projection scale, particles stop speeding up on screen
    var FIRE_SENSORS = [                      // FIRMS satellite codes and display names
        {code: "N", name: "S-NPP"},
        {code: "N20", name: "NOAA-20"},
        {code: "N21", name: "NOAA-21"}
    ];
    var FIRE_FRP_CLASSES = [1, 3, 10, 30, 100, 300];  // one point color per half decade of FRP (MW)
    var FIRE_POINT_SIZE = 3;                  // hotspot point size, until zoomed in far enough to draw the 375 m
                                              // pixel itself (pixels)
    var FIRE_AREA_FILL = "rgba(255, 80, 20, 0.2)";      // FEDS fire perimeters: an orange-red that shows on both
    var FIRE_AREA_STROKE = "rgb(255, 80, 20)";          // the dark globe and the light relief map
    var FRAME_RUNS_SHOWN = 5;                 // model runs listed in the heat frames panel, best first
    var FRAME_PLAY_MS = 1200;                 // time each heat frame is shown while playing
    var FRAME_MODEL_STROKE = "rgb(120, 230, 255)";      // outline of cells a model run predicts VIIRS detects
    var FIRE_AREA_LINE_WIDTH = 1.5;
    var FIRE_AREA_MIN_SIZE = 3;                         // perimeters smaller than this on the screen are not drawn
    var FIRE_LABEL_FONT = "14px mplus-2p-light-sub, sans-serif";
    var FIRE_LABEL_GAP = 4;                             // space kept between fire labels (pixels)
    var FIRE_NAVIGATOR_COUNT = 5;                       // largest fires listed in the layers panel
    var FIRE_NAVIGATOR_MAX_DAYS = 90;                   // longer-burning "fires" are mostly flares and industry
    var FIRE_NAVIGATOR_SHARE = 0.6;                     // going to a fire zooms it to this share of the open view
    var FIRE_NAVIGATOR_MAX_SCALE = 200000;
    var TRANSITION_OLD_FILL = "rgba(255, 80, 20, 0.07)";  // fire-area transitions: the earlier perimeter, faint and
    var TRANSITION_OLD_STROKE = "rgba(255, 80, 20, 0.5)";  // dashed, under the later one drawn as usual
    var TRANSITION_NEW_FIRE = "rgb(255, 225, 40)";        // fires only in the later snapshot
    var TRANSITION_OUT_FIRE = "rgb(170, 170, 170)";       // fires only in the earlier snapshot, dashed
    var TRANSITION_UNLISTED_FIRE = "rgba(255, 80, 20, 0.35)";  // earlier fires missing from the later snapshot
                                                          // only (listed again after it), dotted
    var TRANSITION_ARROW = "rgb(255, 255, 255)";
    var TRANSITION_SAMPLES = 400;             // points sampled along each later perimeter, at most
    var TRANSITION_MIN_STEP_KM = 0.1;         // and at least this far apart
    var TRANSITION_MIN_ADVANCE_KM = 0.375;    // smaller advances (one VIIRS pixel) are not drawn
    var TRANSITION_ARROW_SPACING = 40;        // arrow tips are at least this far apart on the screen (pixels)
    var TRANSITION_ARROW_MIN_LENGTH = 8;      // shorter arrows on the screen are not drawn (pixels)
    var TRANSITION_ARROWS_PER_FIRE = 24;
    var TRANSITION_MAX_GAP = 24 * HOUR;       // "previous snapshot" means at most this much earlier
    var SNAPSHOT_INTERVAL = 12 * HOUR;        // FEDS snapshots are 12-hourly, at 00Z and 12Z
    var FIRE_COLOR_BOOST = 4;                 // points take the heat color of FRP x this, so they read brighter
                                              // than the smoothed fire-heat overlay of the same fires

    // Map tile underlays, served and cached by dev-server.js. See gpu.createTileRenderer for the fields.
    var UNDERLAYS = {
        map: {url: ["/tiles/relief/{z}/{x}/{y}", "/tiles/hydro/{z}/{x}/{y}"], minZoom: 1, maxZoom: 16, base: 2,
            credit: "USGS The National Map: shaded relief and hydrography"},
        satellite: {url: "/tiles/satellite/{z}/{x}/{y}", minZoom: 1, maxZoom: 16, base: 2,
            credit: "USGS The National Map: Orthoimagery"},
        fuel: {url: "/tiles/fuel/{z}/{x}/{y}", minZoom: 3, maxZoom: 13, bounds: [-128, 22, -64, 51], nearest: true,
            credit: "LANDFIRE LF2025 FBFM40, 30 m, lower 48 only"},
        heatday: thermalUnderlay("Day"),
        heatnight: thermalUnderlay("Night"),
        fireband: fireBandUnderlay("Day"),
        firebandnight: fireBandUnderlay("Night")
    };
    // Scott and Burgan (2005) fuel model groups, keyed by FBFM40 code / 10: 91-99 NB, 101-109 GR, ..., 201-204 SB.
    var FUEL_GROUPS = {
        9: {prefix: "NB", name: "Non-burnable", legend: "NB9"},
        10: {prefix: "GR", name: "Grass", legend: "GR2"},
        12: {prefix: "GS", name: "Grass-shrub", legend: "GS2"},
        14: {prefix: "SH", name: "Shrub", legend: "SH5"},
        16: {prefix: "TU", name: "Timber-understory", legend: "TU5"},
        18: {prefix: "TL", name: "Timber litter", legend: "TL8"},
        20: {prefix: "SB", name: "Slash-blowdown", legend: "SB2"}
    };
    var NON_BURNABLE = {1: "Urban", 2: "Snow/ice", 3: "Agriculture", 8: "Water", 9: "Barren"};

    var TRANSPARENT_BLACK = [0, 0, 0, 0];     // singleton 0 rgba
    var REMAINING = "▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫▫";   // glyphs for remaining progress bar
    var COMPLETED = "▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪▪";   // glyphs for completed progress bar

    /**
     * The map is laid out in the screen pixels the page loaded with, so browser zoom changes only the menu: #display
     * is sized in those pixels and scaled by 1 / zoom to fill the window. A real resize of the window lays the map
     * out again at the new size.
     */
    var BASE_PIXEL_RATIO = window.devicePixelRatio || 1;
    var RESIZE_TOLERANCE = 2;  // a pure browser zoom can change the measured size by rounding (pixels)

    function browserZoom() {
        return (window.devicePixelRatio || 1) / BASE_PIXEL_RATIO;
    }

    function measureView() {
        var size = µ.view(), zoom = browserZoom();
        return {width: Math.round(size.width * zoom), height: Math.round(size.height * zoom)};
    }

    /**
     * @returns {Array} the mouse position over the map in view pixels, undoing the browser zoom.
     */
    function mouse(node) {
        var p = d3.mouse(node), zoom = browserZoom();
        return [p[0] * zoom, p[1] * zoom];
    }

    /**
     * Sizes the menus, and the boxes beside them, by the root font size: as large as lets the menu and the layers
     * panel's selectors together fit in MENU_SCREEN_SHARE of the screen at 100% browser zoom, without the two
     * overlapping. The menus' rows do not wrap, so their size in CSS pixels scales with the font alone, and view is
     * in the pixels of 100% zoom. Browser zoom then enlarges or shrinks the menu as usual. Runs again once the web
     * font has loaded, as text measured in a fallback font has other widths.
     */
    function fitMenu() {
        var root = document.documentElement, menu = d3.select("#menu").node(), reference = 16;
        var before = root.style.fontSize;
        root.style.fontSize = reference + "px";
        // Both menus' controls share the budget. scrollHeight also measures a collapsed (hidden) menu.
        var controls = d3.select("#layers-controls").node(), layers = d3.select("#layers").node();
        var area = menu.offsetWidth * menu.scrollHeight + controls.offsetWidth * controls.offsetHeight;
        var size = Math.min(
            reference * Math.sqrt(MENU_SCREEN_SHARE * view.width * view.height / area),
            reference * MENU_WIDTH_SHARE * view.width / (menu.offsetWidth + layers.offsetWidth));
        root.style.fontSize = µ.clamp(size, MENU_FONT_RANGE[0], MENU_FONT_RANGE[1]) + "px";
        if (root.style.fontSize !== before) {
            showOverlayDetails(overlayGridsAgent.value());  // its color scales are drawn at the old size
        }
    }

    function fitDisplay() {
        d3.select("#display")
            .style("width", view.width + "px")
            .style("height", view.height + "px")
            .style("transform", "scale(" + 1 / browserZoom() + ")");
    }

    var view = measureView();
    var log = µ.log();

    /**
     * An object to display various types of messages to the user.
     */
    var report = function() {
        var s = d3.select("#status"), p = d3.select("#progress"), total = REMAINING.length;
        return {
            status: function(msg) {
                return s.classed("bad") ? s : s.text(msg);  // errors are sticky until reset
            },
            error: function(err) {
                var msg = err.status ? err.status + " " + err.message : err;
                switch (err.status) {
                    case -1: msg = "Server Down"; break;
                    case 404: msg = "No Data"; break;
                }
                log.error(err);
                return s.classed("bad", true).text(msg);
            },
            reset: function() {
                return s.classed("bad", false).text("");
            },
            progress: function(amount) {  // amount of progress to report in the range [0, 1]
                if (0 <= amount && amount < 1) {
                    var i = Math.ceil(amount * total);
                    var bar = COMPLETED.substr(0, i) + REMAINING.substr(0, total - i);
                    return p.classed("invisible", false).text(bar);
                }
                return p.classed("invisible", true).text("");  // progress complete
            }
        };
    }();

    function newAgent() {
        return µ.newAgent().on({"reject": report.error, "fail": report.error});
    }

    // Construct the page's main internal components:

    var configuration =
        µ.buildConfiguration(globes, products.overlayTypes);  // holds the page's current configuration settings
    var inputController = buildInputController();             // interprets drag/zoom operations
    var meshAgent = newAgent();      // map data for the earth
    var globeAgent = newAgent();     // the model of the globe
    var gridAgent = newAgent();      // the grid of weather data
    var rendererAgent = newAgent();  // the globe SVG renderer
    var fieldAgent = newAgent();     // the interpolated wind vector field
    var animatorAgent = newAgent();  // the wind animator
    var overlayAgent = newAgent();   // color overlay over the animation
    var overlayGridsAgent = newAgent();  // the grids of the overlays switched on, as {grids:, list:}
    var fireAgent = newAgent();      // VIIRS hotspots for the grid's day
    var areaAgent = newAgent();      // FEDS fire perimeters for the grid's day
    var transitionAgent = newAgent();  // how the FEDS perimeters changed between two snapshots, when asked for
    var framesAgent = newAgent();    // heat frames (observed and modelled heat per overpass) for the grid's day

    // Draws the overlay on the GPU when the projection allows, so moving the globe needs no re-interpolation.
    var gpuOverlay = gpu.createOverlayRenderer(d3.select("#overlay-gl").node(), OVERLAY_ALPHA);
    var tileUnderlay = gpu.createTileRenderer(d3.select("#underlay").node(), UNDERLAYS);

    /**
     * The input controller is an object that translates move operations (drag and/or zoom) into mutations of the
     * current globe's projection, and emits events so other page components can react to these move operations.
     *
     * D3's built-in Zoom behavior is used to bind to the document's drag/zoom events, and the input controller
     * interprets D3's events as move operations on the globe. This method is complicated due to the complex
     * event behavior that occurs during drag and zoom.
     *
     * D3 move operations usually occur as "zoomstart" -> ("zoom")* -> "zoomend" event chain. During "zoom" events
     * the scale and mouse may change, implying a zoom or drag operation accordingly. These operations are quite
     * noisy. What should otherwise be one smooth continuous zoom is usually comprised of several "zoomstart" ->
     * "zoom" -> "zoomend" event chains. A debouncer is used to eliminate the noise by waiting a short period of
     * time to ensure the user has finished the move operation.
     *
     * The "zoom" events may not occur; a simple click operation occurs as: "zoomstart" -> "zoomend". There is
     * additional logic for other corner cases, such as spurious drags which move the globe just a few pixels
     * (most likely unintentional), and the tendency for some touch devices to issue events out of order:
     * "zoom" -> "zoomstart" -> "zoomend".
     *
     * This object emits clean "moveStart" -> ("move")* -> "moveEnd" events for move operations, and "click" events
     * for normal clicks. Spurious moves emit no events.
     */
    function buildInputController() {
        var globe, op = null;

        /**
         * @returns {Object} an object to represent the state for one move operation.
         */
        function newOp(startMouse, startScale) {
            return {
                type: "click",  // initially assumed to be a click operation
                startMouse: startMouse,
                startScale: startScale,
                manipulator: globe.manipulator(startMouse, startScale)
            };
        }

        var zoom = d3.behavior.zoom()
            .on("zoomstart", function() {
                op = op || newOp(mouse(this), zoom.scale());  // a new operation begins
            })
            .on("zoom", function() {
                var currentMouse = mouse(this), currentScale = d3.event.scale;
                op = op || newOp(currentMouse, 1);  // Fix bug on some browsers where zoomstart fires out of order.
                if (op.type === "click" || op.type === "spurious") {
                    var distanceMoved = µ.distance(currentMouse, op.startMouse);
                    if (currentScale === op.startScale && distanceMoved < MIN_MOVE) {
                        // to reduce annoyance, ignore op if mouse has barely moved and no zoom is occurring
                        op.type = distanceMoved > 0 ? "click" : "spurious";
                        return;
                    }
                    dispatch.trigger("moveStart");
                    op.type = "drag";
                }
                if (currentScale != op.startScale) {
                    op.type = "zoom";  // whenever a scale change is detected, (stickily) switch to a zoom operation
                }

                // when zooming, ignore whatever the mouse is doing--really cleans up behavior on touch devices
                op.manipulator.move(op.type === "zoom" ? null : currentMouse, currentScale);
                dispatch.trigger("move");
            })
            .on("zoomend", function() {
                op.manipulator.end();
                if (op.type === "click") {
                    dispatch.trigger("click", op.startMouse, globe.projection.invert(op.startMouse) || []);
                }
                else if (op.type !== "spurious") {
                    signalEnd();
                }
                op = null;  // the drag/zoom/click operation is over
            });

        var signalEnd = _.debounce(function() {
            if (!op || op.type !== "drag" && op.type !== "zoom") {
                configuration.save({orientation: globe.orientation()}, {source: "moveEnd"});
                dispatch.trigger("moveEnd");
            }
        }, MOVE_END_WAIT);  // wait for a bit to decide if user has stopped moving the globe

        d3.select("#display").call(zoom);
        d3.select("#show-location").on("click", function() {
            if (navigator.geolocation) {
                report.status("Finding current position...");
                navigator.geolocation.getCurrentPosition(function(pos) {
                    report.status("");
                    var coord = [pos.coords.longitude, pos.coords.latitude], rotate = globe.locate(coord);
                    if (rotate) {
                        globe.projection.rotate(rotate);
                        configuration.save({orientation: globe.orientation()});  // triggers reorientation
                    }
                    dispatch.trigger("click", globe.projection(coord), coord);
                }, log.error);
            }
        });

        function reorient() {
            var options = arguments[3] || {};
            if (!globe || options.source === "moveEnd") {
                // reorientation occurred because the user just finished a move operation, so globe is already
                // oriented correctly.
                return;
            }
            dispatch.trigger("moveStart");
            globe.orientation(configuration.get("orientation") || regionOrientation(globe), view);
            zoom.scale(globe.projection.scale());
            dispatch.trigger("moveEnd");
        }

        var dispatch = _.extend({
            globe: function(_) {
                if (_) {
                    globe = _;
                    zoom.scaleExtent(globe.scaleExtent());
                    reorient();
                }
                return _ ? this : globe;
            }
        }, Backbone.Events);
        return dispatch.listenTo(configuration, "change:orientation", reorient);
    }

    /**
     * @param resource the GeoJSON resource's URL
     * @returns {Object} a promise for GeoJSON topology features: {boundaryLo:, boundaryHi:}
     */
    function buildMesh(resource) {
        var cancel = this.cancel;
        report.status("Downloading...");
        return µ.loadJson(resource).then(function(topo) {
            if (cancel.requested) return null;
            log.time("building meshes");
            var o = topo.objects;
            var coastLo = topojson.feature(topo, µ.isMobile() ? o.coastline_tiny : o.coastline_110m);
            var coastHi = topojson.feature(topo, µ.isMobile() ? o.coastline_110m : o.coastline_50m);
            var lakesLo = topojson.feature(topo, µ.isMobile() ? o.lakes_tiny : o.lakes_110m);
            var lakesHi = topojson.feature(topo, µ.isMobile() ? o.lakes_110m : o.lakes_50m);
            log.timeEnd("building meshes");
            return {
                coastLo: coastLo,
                coastHi: coastHi,
                lakesLo: lakesLo,
                lakesHi: lakesHi
            };
        });
    }

    /**
     * @param {String} projectionName the desired projection's name.
     * @returns {Object} a promise for a globe object.
     */
    /**
     * @returns {String} the orientation "lon,lat,scale" at which the globe starts: over the chosen region.
     */
    function regionOrientation(globe) {
        var region = products.region(configuration.get("region"));
        return [region.center[0], region.center[1], Math.round(globe.fit(view) * region.zoom)].join(",");
    }

    function buildGlobe(projectionName) {
        var builder = globes.get(projectionName);
        if (!builder) {
            return when.reject("Unknown projection: " + projectionName);
        }
        var globe = builder(view);
        globe.projectionName = projectionName;
        return when(globe);
    }

    // Some hacky stuff to ensure only one download can be in progress at a time.
    var downloadsInProgress = 0;

    function buildGrids() {
        report.status("Downloading...");
        log.time("build grids");
        // UNDONE: upon failure to load a product, the unloaded product should still be stored in the agent.
        //         this allows us to use the product for navigation and other state.
        var cancel = this.cancel;
        downloadsInProgress++;
        // Only the primary product (wind or currents): overlays load separately, see buildOverlayGrids.
        var attr = _.extend({}, configuration.attributes, {overlayType: "default"});
        var loaded = when.map(products.productsFor(attr), function(product) {
            return product.load(cancel);
        });
        return when.all(loaded).then(function(products) {
            log.time("build grids");
            return {primaryGrid: products[0]};
        }).ensure(function() {
            downloadsInProgress--;
        });
    }

    /**
     * @returns {Array} the overlay types switched on, in draw order. The configuration's overlayType is "off",
     *          "default" (the primary product's own overlay, such as wind speed), or a list such as "wind,fire_heat".
     */
    function overlayTypes() {
        var value = configuration.get("overlayType") || "default";
        if (value === "off") return [];
        if (value === "default") return [configuration.get("param") === "ocean" ? "currents" : "wind"];
        return value.split(",");
    }

    // Loaded overlay grids by data path, so switching an overlay off and on again does not download it again.
    var overlayGridCache = {};

    /**
     * Loads the grids of the overlays switched on. The primary grid serves as its own overlay (wind speed).
     *
     * @returns {Object} a promise for {grids: the grids these overlays go with, list: [grid, ...]}.
     */
    function buildOverlayGrids(grids) {
        if (!grids) return null;
        var cancel = this.cancel, primary = grids.primaryGrid, kept = {};
        var loads = overlayTypes().map(function(type) {
            if (type === primary.type) return primary;
            var attr = _.extend({}, configuration.attributes, {overlayType: type});
            var product = _.find(products.productsFor(attr), function(p) { return p.type === type; });
            if (!product) return null;
            var key = product.region.key + "|" + product.paths.join("|");
            kept[key] = overlayGridCache[key] || product.load({}).otherwise(function(err) {
                delete overlayGridCache[key];  // try again next time
                report.error(err);
                return null;
            });
            return kept[key];
        });
        overlayGridCache = kept;  // forget overlays no longer shown
        return when.all(loads).then(function(list) {
            return cancel.requested ? null : {grids: grids, list: list.filter(µ.isValue)};
        });
    }

    /**
     * Modifies the configuration to navigate to the chronologically next or previous data layer.
     */
    function navigate(step) {
        if (downloadsInProgress > 0) {
            log.debug("Download in progress--ignoring nav request.");
            return;
        }
        var next = gridAgent.value().primaryGrid.navigate(step);
        if (next) {
            configuration.save(µ.dateToConfig(next));
        }
    }

    function buildRenderer(mesh, globe) {
        if (!mesh || !globe) return null;

        report.status("Rendering Globe...");
        log.time("rendering map");

        // UNDONE: better way to do the following?
        var dispatch = _.clone(Backbone.Events);
        if (rendererAgent._previous) {
            rendererAgent._previous.stopListening();
        }
        rendererAgent._previous = dispatch;

        // First clear map and foreground svg contents.
        µ.removeChildren(d3.select("#map").node());
        µ.removeChildren(d3.select("#foreground").node());
        // Create new map svg elements.
        globe.defineMap(d3.select("#map"), d3.select("#foreground"));

        var path = d3.geo.path().projection(globe.projection).pointRadius(7);
        var coastline = d3.select(".coastline");
        var lakes = d3.select(".lakes");
        d3.selectAll("path").attr("d", path);  // do an initial draw -- fixes issue with safari

        function drawLocationMark(point, coord) {
            // show the location on the map if defined
            if (fieldAgent.value() && !fieldAgent.value().isInsideBoundary(point[0], point[1])) {
                // UNDONE: Sometimes this is invoked on an old, released field, because new one has not been
                //         built yet, causing the mark to not get drawn.
                return;  // outside the field boundary, so ignore.
            }
            if (coord && _.isFinite(coord[0]) && _.isFinite(coord[1])) {
                var mark = d3.select(".location-mark");
                if (!mark.node()) {
                    mark = d3.select("#foreground").append("path").attr("class", "location-mark");
                }
                mark.datum({type: "Point", coordinates: coord}).attr("d", path);
            }
        }

        // Draw the location mark if one is currently visible.
        if (activeLocation.point && activeLocation.coord) {
            drawLocationMark(activeLocation.point, activeLocation.coord);
        }

        // Throttled draw method helps with slow devices that would get overwhelmed by too many redraw events.
        var REDRAW_WAIT = 5;  // milliseconds
        var doDraw_throttled = _.throttle(doDraw, REDRAW_WAIT, {leading: false});

        function doDraw() {
            d3.selectAll("path").attr("d", path);
            rendererAgent.trigger("redraw");
            doDraw_throttled = _.throttle(doDraw, REDRAW_WAIT, {leading: false});
        }

        // Attach to map rendering events on input controller.
        dispatch.listenTo(
            inputController, {
                moveStart: function() {
                    coastline.datum(mesh.coastLo);
                    lakes.datum(mesh.lakesLo);
                    rendererAgent.trigger("start");
                },
                move: function() {
                    doDraw_throttled();
                },
                moveEnd: function() {
                    coastline.datum(mesh.coastHi);
                    lakes.datum(mesh.lakesHi);
                    d3.selectAll("path").attr("d", path);
                    rendererAgent.trigger("render");
                },
                click: drawLocationMark
            });

        // Finally, inject the globe model into the input controller. Do it on the next event turn to ensure
        // renderer is fully set up before events start flowing.
        when(true).then(function() {
            inputController.globe(globe);
        });

        log.timeEnd("rendering map");
        return "ready";
    }

    function createMask(globe) {
        if (!globe) return null;

        log.time("render mask");

        // Create a detached canvas, ask the model to define the mask polygon, then fill with an opaque color.
        var width = view.width, height = view.height;
        var canvas = d3.select(document.createElement("canvas")).attr("width", width).attr("height", height).node();
        var context = globe.defineMask(canvas.getContext("2d"));
        context.fillStyle = "rgba(255, 0, 0, 1)";
        context.fill();
        // d3.select("#display").node().appendChild(canvas);  // make mask visible for debugging

        var imageData = context.getImageData(0, 0, width, height);
        var data = imageData.data;  // layout: [r, g, b, a, r, g, b, a, ...]
        log.timeEnd("render mask");
        return {
            imageData: imageData,
            isVisible: function(x, y) {
                var i = (y * width + x) * 4;
                return data[i + 3] > 0;  // non-zero alpha means pixel is visible
            },
            set: function(x, y, rgba) {
                var i = (y * width + x) * 4;
                data[i    ] = rgba[0];
                data[i + 1] = rgba[1];
                data[i + 2] = rgba[2];
                data[i + 3] = rgba[3];
                return this;
            }
        };
    }

    /**
     * Builds a field that answers queries by inverting the projection on demand. The globe's projection is mutated
     * in place while the user drags or zooms, so this field stays valid across moves and is only rebuilt when the
     * globe or the grids change. Particles move in [λ, φ] (see animate). The overlay is drawn on the GPU when the
     * projection allows it, otherwise on the CPU after each move (see drawOverlay).
     */
    function createField(globe, grids) {
        if (!globe || !grids) return null;
        report.status("");
        var projection = globe.projection, primaryGrid = grids.primaryGrid;

        // Some inverses clamp instead of failing (orthographic maps points past the disc's edge onto the limb), so
        // a point counts as on the globe only if projecting its inverse lands back on it.
        function coordAt(x, y) {
            var coord = projection.invert([x, y]);
            if (!coord || !_.isFinite(coord[0]) || !_.isFinite(coord[1])) return null;
            var p = projection(coord);
            return p && Math.abs(p[0] - x) < 1 && Math.abs(p[1] - y) < 1 ? coord : null;
        }

        return {
            globe: globe,
            grids: grids,
            gpu: !!gpuOverlay && gpuOverlay.supports(globe.projectionName),
            coordAt: coordAt,
            isInsideBoundary: function(x, y) {
                return !!coordAt(x, y);
            },
            isDefined: function(x, y) {
                var coord = coordAt(x, y);
                return !!coord && µ.isValue(primaryGrid.interpolate(coord[0], coord[1]));
            }
        };
    }

    /**
     * Animates particles that live in [λ, φ] and are projected anew on every frame, so they keep flowing while the
     * globe moves. A particle moves wind * velocityScale degrees of arc per frame, which gives the same screen speed
     * as earth's original per-pixel field. On slow machines the particle count drops until frames fit the budget.
     * With flow "smoke", particles start at the hotspots in view instead of anywhere, and fade as they age.
     */
    function animate(field) {
        if (!field) return;

        var cancel = this.cancel, globe = field.globe, projection = globe.projection;
        var primaryGrid = field.grids.primaryGrid, rad = Math.PI / 180;
        // maxIntensity is the velocity at which particle color intensity is maximum
        var colorStyles = µ.windIntensityColorScale(INTENSITY_SCALE_STEP, primaryGrid.particles.maxIntensity);
        var buckets = colorStyles.map(function() { return []; });
        // Smoke is drawn by age instead of speed, as wide soft strokes that pile up into a cloud: dense and narrow
        // where it leaves the fire, wider and thinner downwind.
        var smokeStyles = colorStyles.map(function(style, i) {
            var t = i / (colorStyles.length - 1);
            return "rgba(215, 215, 220, " + µ.spread(t, SMOKE_ALPHA[0], SMOKE_ALPHA[1]).toFixed(3) + ")";
        });
        var smokeWidths = colorStyles.map(function(style, i) {
            return µ.spread(i / (colorStyles.length - 1), SMOKE_WIDTH[0], SMOKE_WIDTH[1]);
        });
        var fadeFillStyle = µ.isFF() ? "rgba(0, 0, 0, 0.95)" : "rgba(0, 0, 0, 0.97)";  // FF Mac alpha behaves oddly

        // The visible extent and particle speed depend on zoom, so refresh them whenever the scale changes.
        // Returns the ratio of the new scale to the old one, or 1 if the zoom has not changed.
        var bounds, velocityScale, scale = null;
        function measure() {
            var previous = scale;
            if (projection.scale() === previous) return 1;
            scale = projection.scale();
            bounds = globe.bounds(view);
            velocityScale = bounds.height * primaryGrid.particles.velocityScale *
                Math.min(1, PARTICLE_SPEED_SCALE_LIMIT / scale);
            return previous ? scale / previous : 1;
        }
        measure();

        // A point is visible if it projects onto the screen and the inverse leads back to it. The inverse fails that
        // test on the far side of the globe and in areas the projection clips.
        function visiblePoint(λ, φ) {
            var p = projection([λ, φ]);
            if (!p || !(bounds.x <= p[0] && p[0] <= bounds.xMax && bounds.y <= p[1] && p[1] <= bounds.yMax)) {
                return null;
            }
            var back = projection.invert(p);
            return back && Math.abs(back[1] - φ) < 0.01 && Math.abs(µ.floorMod(back[0] - λ + 180, 360) - 180) < 0.01 ?
                p : null;
        }

        // Hotspots in view, as smoke sources: [λ, φ, ...] detections, and the running sum of their weights.
        var emitters = [], emitterWeights = [], emitterView = null, emitterFires = null, emitterAge = 0;
        function findEmitters() {
            var fires = fireAgent.value(), currentView = viewKey();
            emitterAge++;
            if (fires === emitterFires && (currentView === emitterView || emitterAge < SMOKE_EMITTER_REFRESH)) return;
            emitterFires = fires;
            emitterView = currentView;
            emitterAge = 0;
            emitters = [];
            emitterWeights = [];
            var total = 0;
            (fires ? fires.detections : []).forEach(function(d) {
                if (visiblePoint(d[0], d[1])) {
                    // More smoke from more powerful fires, but not so much more that small fires show none.
                    total += Math.sqrt(Math.max(d[3], 0.1));
                    emitters.push(d);
                    emitterWeights.push(total);
                }
            });
        }

        function spawnSmoke(particle) {
            var total = _.last(emitterWeights);
            if (!total) {
                particle.λ = particle.φ = NaN;  // no hotspots in view; tries again when it respawns
                return particle;
            }
            var d = emitters[Math.min(_.sortedIndex(emitterWeights, Math.random() * total), emitters.length - 1)];
            var spread = (d[4] || 0.375) / 111.2;  // anywhere in the detection's pixel (km to degrees)
            particle.λ = d[0] + (Math.random() - 0.5) * spread / Math.cos(d[1] * rad);
            particle.φ = d[1] + (Math.random() - 0.5) * spread;
            return particle;
        }

        function spawn(particle) {
            particle.view = null;  // its last end point is no longer where it is
            if (smoke) return spawnSmoke(particle);
            for (var tries = 0; tries < 30; tries++) {
                var coord = field.coordAt(_.random(bounds.x, bounds.xMax), _.random(bounds.y, bounds.yMax));
                if (coord && primaryGrid.interpolate(coord[0], coord[1])) {
                    particle.λ = coord[0];
                    particle.φ = coord[1];
                    return particle;
                }
            }
            particle.λ = particle.φ = NaN;  // nothing found; interpolate() fails and the particle respawns later
            return particle;
        }

        // The particle count follows the visible width of the globe, up to the budget that adapt() lowers on slow
        // machines.
        var particles = [], budget = Infinity;
        var smoke = configuration.get("flow") === "smoke", maxAge = smoke ? MAX_PARTICLE_AGE * SMOKE_AGE_FACTOR : MAX_PARTICLE_AGE;
        if (smoke) findEmitters();
        function fitParticleCount() {
            var choice = configuration.get("particles");
            var density = _.has(PARTICLE_DENSITY, choice) ? PARTICLE_DENSITY[choice] : PARTICLE_DENSITY.some;
            var target = bounds.width * PARTICLE_MULTIPLIER * density * (µ.isMobile() ? PARTICLE_REDUCTION : 1);
            target = Math.round(target);
            target = Math.min(target, budget);
            if (particles.length > target) {
                particles.length = target;
            }
            while (particles.length < target) {
                particles.push(spawn({age: _.random(0, maxAge)}));
            }
        }
        fitParticleCount();
        log.debug("particle count: " + particles.length);

        /**
         * Keeps particles evenly spread across zooms. Particles stay put on the ground, so zooming out by a ratio r
         * packs them into r² of the view; each is moved to a random visible spot with probability 1 - r². Zooming in
         * needs no culling, as particles that leave the view respawn inside it.
         */
        function rebalance(ratio) {
            if (ratio === 1) return;
            fitParticleCount();
            if (ratio < 1) {
                var keep = ratio * ratio;
                particles.forEach(function(particle) {
                    if (Math.random() > keep) {
                        spawn(particle).age = _.random(0, maxAge);
                    }
                });
            }
        }

        function viewKey() {
            return projection.rotate().concat(projection.scale()).join();
        }

        /**
         * Follows the menu's flow choice, starting every particle afresh when it changes.
         */
        function followFlow() {
            var next = configuration.get("flow") === "smoke";
            if (next === smoke) return;
            smoke = next;
            maxAge = smoke ? MAX_PARTICLE_AGE * SMOKE_AGE_FACTOR : MAX_PARTICLE_AGE;
            emitterFires = undefined;  // find the hotspots now
            if (smoke) findEmitters();
            particles.forEach(function(particle) { spawn(particle).age = _.random(0, maxAge); });
            lastView = null;  // drop the other flow's trails
        }

        function evolve() {
            followFlow();
            if (smoke) findEmitters();
            rebalance(measure());
            fitParticleCount();  // follows the menu's particle choice; does nothing if the count is already right
            var currentView = viewKey(), n = buckets.length;
            buckets.forEach(function(bucket) { bucket.length = 0; });
            particles.forEach(function(particle) {
                if (particle.age > maxAge) {
                    spawn(particle).age = 0;
                }
                var λ = particle.λ, φ = particle.φ;
                var wind = primaryGrid.interpolate(λ, φ), from, to;
                var λt = wind && λ + wind[0] * velocityScale / Math.cos(φ * rad);
                var φt = wind && φ + wind[1] * velocityScale;
                // If the view has not changed, last frame's end point is this frame's start point.
                from = particle.view === currentView ? [particle.xt, particle.yt] : visiblePoint(λ, φ);
                if (wind && Math.abs(φt) < 89 && from && (to = visiblePoint(λt, φt)) &&
                        Math.abs(to[0] - from[0]) + Math.abs(to[1] - from[1]) < MAX_PARTICLE_STEP) {
                    particle.x = from[0];
                    particle.y = from[1];
                    particle.xt = to[0];
                    particle.yt = to[1];
                    particle.view = currentView;
                    particle.λ = λt;
                    particle.φ = φt;
                    buckets[smoke ? Math.min(n - 1, Math.floor(particle.age / maxAge * n)) :
                        colorStyles.indexFor(wind[2])].push(particle);
                    particle.age += 1;
                }
                else {
                    particle.age = maxAge + 1;  // left the view or the grid, so respawn next frame
                    particle.view = null;
                }
            });
        }

        var g = d3.select("#animation").node().getContext("2d");
        g.lineWidth = PARTICLE_LINE_WIDTH;
        g.fillStyle = fadeFillStyle;
        var lastView = null;

        function draw() {
            // Fade existing particle trails, or drop them if the globe moved since they were drawn or particles are off.
            var currentView = viewKey();
            if (currentView !== lastView || particles.length === 0) {
                g.clearRect(0, 0, view.width, view.height);
                lastView = currentView;
            }
            else {
                var prev = g.globalCompositeOperation;
                g.globalCompositeOperation = "destination-in";
                g.fillRect(0, 0, view.width, view.height);
                g.globalCompositeOperation = prev;
            }

            // Draw new particle trails.
            buckets.forEach(function(bucket, i) {
                if (bucket.length > 0) {
                    g.beginPath();
                    g.strokeStyle = smoke ? smokeStyles[i] : colorStyles[i];
                    g.lineWidth = smoke ? smokeWidths[i] : PARTICLE_LINE_WIDTH;
                    // Flat ends: a smoke particle in still air would otherwise pile round caps into a disk.
                    bucket.forEach(function(particle) {
                        g.moveTo(particle.x, particle.y);
                        g.lineTo(particle.xt, particle.yt);
                    });
                    g.stroke();
                }
            });
        }

        // Shed particles when frames run over budget, so slow machines keep a steady frame rate.
        var frames = 0, cost = 0;
        function adapt(elapsed) {
            cost += elapsed;
            if (++frames < PARTICLE_ADAPT_FRAMES) return;
            if (cost / frames > FRAME_RATE * PARTICLE_FRAME_BUDGET && particles.length > MIN_PARTICLE_COUNT) {
                particles.length = budget = Math.max(MIN_PARTICLE_COUNT, Math.round(particles.length * 0.8));
                log.debug("slow frames (" + Math.round(cost / frames) + " ms), particle count: " + particles.length);
            }
            frames = cost = 0;
        }

        (function frame() {
            try {
                if (cancel.requested) return;
                var start = Date.now();
                evolve();
                draw();
                adapt(Date.now() - start);
                setTimeout(frame, FRAME_RATE);
            }
            catch (e) {
                report.error(e);
            }
        })();
    }

    /**
     * Colors the overlays on the CPU, for projections or browsers the GPU path does not handle, blending them
     * bottom to top. Each 2x2 block of pixels takes the color at its corner. Runs in batches so the page stays
     * responsive.
     *
     * @returns {Object} a promise for the overlays' ImageData, or for null if cancelled.
     */
    function renderOverlayImage(field, list, cancel) {
        var globe = field.globe, projection = globe.projection;
        var mask = createMask(globe), bounds = globe.bounds(view);
        var d = when.defer(), x = bounds.x, point = [], color = [0, 0, 0, 0];

        // The color at [λ, φ] of all overlays together, by the "over" operator on premultiplied colors.
        function blend(λ, φ) {
            var r = 0, g = 0, b = 0, a = 0;
            list.forEach(function(grid) {
                var value = grid.interpolate(λ, φ), scalar = grid.field === "vector" ? value && value[2] : value;
                if (!µ.isValue(scalar)) return;
                var rgba = grid.scale.gradient(scalar, OVERLAY_ALPHA), sa = rgba[3] / 255;
                r = rgba[0] * sa + r * (1 - sa);
                g = rgba[1] * sa + g * (1 - sa);
                b = rgba[2] * sa + b * (1 - sa);
                a = sa + a * (1 - sa);
            });
            if (a === 0) return TRANSPARENT_BLACK;
            color[0] = r / a; color[1] = g / a; color[2] = b / a; color[3] = Math.round(a * 255);
            return color;
        }

        function renderColumn(x) {
            for (var y = bounds.y; y <= bounds.yMax; y += 2) {
                if (mask.isVisible(x, y)) {
                    point[0] = x; point[1] = y;
                    var coord = projection.invert(point), c = TRANSPARENT_BLACK;
                    if (coord && _.isFinite(coord[0]) && _.isFinite(coord[1])) {
                        c = blend(coord[0], coord[1]);
                    }
                    mask.set(x, y, c).set(x+1, y, c).set(x, y+1, c).set(x+1, y+1, c);
                }
            }
        }

        log.time("render overlay");
        (function batch() {
            try {
                var start = Date.now();
                while (x < bounds.xMax) {
                    if (cancel.requested) {
                        return d.resolve(null);
                    }
                    renderColumn(x);
                    x += 2;
                    if ((Date.now() - start) > MAX_TASK_TIME) {
                        // Taking too long. Schedule the next batch for later and yield.
                        report.progress((x - bounds.x) / (bounds.xMax - bounds.x));
                        return setTimeout(batch, MIN_SLEEP_TIME);
                    }
                }
                report.progress(1);  // 100% complete
                log.timeEnd("render overlay");
                d.resolve(mask.imageData);
            }
            catch (e) {
                d.reject(e);
            }
        })();
        return d.promise;
    }

    /**
     * Loads the VIIRS hotspots for the UTC day of the grids, made by scripts/firms_to_earth_json.py. A day with no
     * file just has no fires, so a failed load is not reported as an error.
     */
    function loadFires(grids) {
        if (!grids) return null;
        var cancel = this.cancel;
        var date = configuration.get("date") === "current" ? "current" : grids.primaryGrid.date;
        return µ.loadJson(products.firePath(date)).then(function(fires) {
            if (cancel.requested) return null;
            // Flatten to [λ, φ, sprite index, FRP, pixel size (km)], drawing the hottest detections last.
            var detections = [];
            _.each(fires.sensors, function(rows) {
                rows.forEach(function(row) {
                    var size = row[5] && row[6] ? Math.sqrt(row[5] * row[6]) : 0;
                    detections.push([row[0], row[1], fireClass(row[2]), row[2], size]);
                });
            });
            fires.detections = _.sortBy(detections, function(d) { return d[3]; });
            fires.passDates = firePassDates(fires);
            return fires;
        }).otherwise(function() {
            return null;
        });
    }

    /**
     * @returns {Object} {Day: "2026-10-28", Night: ...}: the UTC day with the most detections from daytime (06-18
     *          local solar time) and from nighttime overpasses, as GIBS dates its daily day and night mosaics.
     */
    function firePassDates(fires) {
        var counts = {Day: {}, Night: {}};
        _.each(fires.sensors, function(rows) {
            rows.forEach(function(row) {
                var time = new Date(row[3] * 1000);
                var solarHour = µ.floorMod(time.getUTCHours() + time.getUTCMinutes() / 60 + row[0] / 15, 24);
                var day = counts[solarHour >= 6 && solarHour < 18 ? "Day" : "Night"], ymd = µ.dateToUTCymd(time, "-");
                day[ymd] = (day[ymd] || 0) + 1;
            });
        });
        return _.object(_.map(counts, function(days, pass) {
            var ymds = _.keys(days);
            return [pass, ymds.length ? _.max(ymds, function(ymd) { return days[ymd]; }) : null];
        }));
    }

    /**
     * @returns {Number} index into FIRE_FRP_CLASSES of the class nearest the FRP on a log scale.
     */
    function fireClass(frp) {
        var i = Math.round(Math.log(Math.max(frp, 1)) / Math.LN10 * 2);
        return Math.min(i, FIRE_FRP_CLASSES.length - 1);
    }

    /**
     * @returns {Array} the [r, g, b] of each FRP class, on the same color scale as the fire-heat overlay.
     */
    var fireColors = _.memoize(function() {
        return FIRE_FRP_CLASSES.map(function(frp) {
            return products.fireHeatScale.gradient(frp * FIRE_COLOR_BOOST, 255).slice(0, 3);
        });
    });

    /**
     * @returns {Function} f(λ, φ) that is true if the projection shows the point. On the orthographic globe that is
     *          the hemisphere facing the viewer; other projections are asked whether the inverse leads back to it.
     */
    function visibilityTest(globe) {
        var projection = globe.projection, rad = Math.PI / 180;
        if (globe.projectionName === "orthographic") {
            var rotate = projection.rotate(), λc = -rotate[0] * rad, φc = -rotate[1] * rad;
            var sinφc = Math.sin(φc), cosφc = Math.cos(φc);
            return function(λ, φ) {
                return Math.sin(φ * rad) * sinφc + Math.cos(φ * rad) * cosφc * Math.cos(λ * rad - λc) > 0;
            };
        }
        return function(λ, φ) {
            var back = projection.invert(projection([λ, φ]));
            return !!back && Math.abs(back[1] - φ) < 0.01 && Math.abs(µ.floorMod(back[0] - λ + 180, 360) - 180) < 0.01;
        };
    }

    /**
     * Loads the FEDS fire perimeters for the UTC day of the grids, made by scripts/feds_to_earth_json.py, noting each
     * fire's bounding box for finding the fire at a clicked point. A day with no file has no outlines.
     */
    function loadFireAreas(grids) {
        if (!grids) return null;
        var cancel = this.cancel;
        var date = configuration.get("date") === "current" ? "current" : grids.primaryGrid.date;
        return µ.loadJson(products.fireAreaPath(date)).then(function(areas) {
            if (cancel.requested) return null;
            areas.features.forEach(function(feature) {
                feature.bbox = d3.geo.bounds(feature);
            });
            return areas;
        }).otherwise(function() {
            return null;
        });
    }

    /**
     * @returns {Promise} the FEDS snapshots listed by scripts/feds_to_earth_json.py --index: {snapshots: [time, ...]
     *          oldest first, e.g. "2026-08-01T00:00:00", listed: {time: {id: true}}} the fires in each, or no
     *          snapshots if there is no index.
     */
    var snapshotIndex = _.memoize(function() {
        return µ.loadJson(products.fireAreaIndexPath()).then(function(index) {
            var listed = {};
            _.each(index.fires || {}, function(ids, time) {
                listed[time] = _.object(ids, ids.map(_.constant(true)));
            });
            return {snapshots: index.snapshots, listed: listed};
        }).otherwise(function() {
            return {snapshots: [], listed: {}};
        });
    });

    /**
     * @returns {Promise} the FEDS snapshot at the time, with each fire's bounding box as for loadFireAreas. A fire
     *          listed more than once (older files from the large-fire collection) is kept once, so areas add up.
     */
    var loadSnapshot = _.memoize(function(time) {
        return µ.loadJson(products.fireAreaSnapshotPath(time)).then(function(areas) {
            var seen = {};
            areas.features = areas.features.filter(function(feature) {
                return !seen[feature.properties.id] && (seen[feature.properties.id] = true);
            });
            areas.features.forEach(function(feature) {
                feature.bbox = d3.geo.bounds(feature);
            });
            return areas;
        });
    });

    function snapshotMillis(time) {
        return Date.parse(time.substr(0, 13) + ":00:00Z");
    }

    /**
     * @returns {String} "2026-08-02T00": a snapshot time as the URL's transition= option writes it.
     */
    function snapshotKey(time) {
        return time.substr(0, 13);
    }

    /**
     * @returns {String} "08-02 00Z": a snapshot time as the menu shows it.
     */
    function snapshotLabel(time) {
        return time.substr(5, 5) + " " + time.substr(11, 2) + "Z";
    }

    /**
     * @returns {Object} {from:, to:} the snapshot times the configuration's transition compares, or {error:} saying
     *          why there are none. "prev" compares the latest snapshot at or before the page's time (within 12 h)
     *          with the one before it (within 24 h); "FROM,TO" names both.
     */
    function resolveTransition(times, attr) {
        if (attr.transition === "prev") {
            var now = attr.date === "current" ? Infinity :
                Date.UTC(+attr.date.substr(0, 4), +attr.date.substr(5, 2) - 1, +attr.date.substr(8, 2),
                    +attr.hour.substr(0, 2));
            var i = times.length - 1;
            while (i >= 0 && snapshotMillis(times[i]) > now) i--;
            if (i < 0 || now - snapshotMillis(times[i]) >= SNAPSHOT_INTERVAL) {
                return {error: "No FEDS snapshot in the 12 h up to this time. Pick two instead."};
            }
            if (i === 0 || snapshotMillis(times[i]) - snapshotMillis(times[i - 1]) > TRANSITION_MAX_GAP) {
                return {to: times[i], error: "No FEDS snapshot in the day before " + snapshotLabel(times[i]) + "."};
            }
            return {from: times[i - 1], to: times[i]};
        }
        var keys = attr.transition.split(","), found = keys.map(function(key) {
            return _.find(times, function(time) { return snapshotKey(time) === key; });
        });
        var result = {from: found[0], to: found[1]};
        if (!result.from || !result.to) {
            result.error = "No FEDS snapshot listed for " + keys.filter(function(key, i) {
                return !found[i];
            }).join(" or ").replace("T", " ") + "Z.";
        }
        else if (snapshotMillis(result.from) >= snapshotMillis(result.to)) {
            result.error = "The first snapshot must come before the second.";
        }
        return result;
    }

    /**
     * @returns {Array} the (Multi)Polygon's rings, holes included, or only each polygon's outer ring.
     */
    function ringsOf(geometry, outerOnly) {
        var result = [];
        (geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates).forEach(function(rings) {
            result.push.apply(result, outerOnly ? rings.slice(0, 1) : rings);
        });
        return result;
    }

    /**
     * @returns {Boolean} true if the two fires overlap: their boxes do, and a vertex of one lies in the other.
     */
    function firesOverlap(a, b) {
        var p = a.bbox, q = b.bbox;
        if (p[1][0] < q[0][0] || q[1][0] < p[0][0] || p[1][1] < q[0][1] || q[1][1] < p[0][1]) return false;
        var anyInside = function(x, y) {
            return ringsOf(x.geometry, true).some(function(ring) {
                return ring.some(function(c) { return polygonContains(y.geometry, c[0], c[1]); });
            });
        };
        return anyInside(a, b) || anyInside(b, a);
    }

    /**
     * Finds where a fire advanced past its earlier perimeter(s): points every TRANSITION_MIN_STEP_KM or more along
     * the later perimeter (TRANSITION_SAMPLES at most) that lie outside the earlier one, each with the nearest point
     * of the earlier perimeter, kept if over TRANSITION_MIN_ADVANCE_KM apart. Distances are in km on a plane
     * tangent at the fire, which is close enough for fires tens of km across.
     *
     * @returns {Array} [{from: [λ, φ], to: [λ, φ], km:, bearing: (degrees from north)}], largest
     *          first, with tail and tip [x, y] in km for spacing them out.
     */
    function fireAdvances(feature, old) {
        var b = feature.bbox, λ0 = (b[0][0] + b[1][0]) / 2, φ0 = (b[0][1] + b[1][1]) / 2;
        var kx = 111.32 * Math.cos(φ0 * Math.PI / 180), ky = 110.57;  // km per degree
        var toKm = function(c) { return [(c[0] - λ0) * kx, (c[1] - φ0) * ky]; };
        var toLonLat = function(p) { return [p[0] / kx + λ0, p[1] / ky + φ0]; };
        var oldRings = [];
        old.forEach(function(f) {
            ringsOf(f.geometry).forEach(function(ring) { oldRings.push(ring.map(toKm)); });
        });
        var outer = ringsOf(feature.geometry, true).map(function(ring) { return ring.map(toKm); });
        var length = 0;
        outer.forEach(function(ring) {
            for (var i = 1; i < ring.length; i++) {
                length += Math.sqrt(Math.pow(ring[i][0] - ring[i - 1][0], 2) + Math.pow(ring[i][1] - ring[i - 1][1], 2));
            }
        });
        var step = Math.max(length / TRANSITION_SAMPLES, TRANSITION_MIN_STEP_KM), advances = [];

        function consider(p) {
            var ll = toLonLat(p);
            if (old.some(function(f) { return polygonContains(f.geometry, ll[0], ll[1]); })) return;
            var best = Infinity, nearest = null;
            oldRings.forEach(function(ring) {
                for (var i = 1; i < ring.length; i++) {
                    var a = ring[i - 1], dx = ring[i][0] - a[0], dy = ring[i][1] - a[1], d2 = dx * dx + dy * dy;
                    var t = d2 > 0 ? µ.clamp(((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / d2, 0, 1) : 0;
                    var x = a[0] + t * dx, y = a[1] + t * dy, e2 = (p[0] - x) * (p[0] - x) + (p[1] - y) * (p[1] - y);
                    if (e2 < best) {
                        best = e2;
                        nearest = [x, y];
                    }
                }
            });
            var km = Math.sqrt(best);
            if (!nearest || km < TRANSITION_MIN_ADVANCE_KM) return;
            var bearing = µ.floorMod(Math.atan2(p[0] - nearest[0], p[1] - nearest[1]) * 180 / Math.PI, 360);
            advances.push({from: toLonLat(nearest), to: ll, tail: nearest, tip: p, km: km, bearing: bearing});
        }

        outer.forEach(function(ring) {
            var carry = 0, s = 0;  // carry: distance into the next segment of the next sample
            for (var i = 1; i < ring.length; i++) {
                var a = ring[i - 1], c = ring[i];
                var seg = Math.sqrt(Math.pow(c[0] - a[0], 2) + Math.pow(c[1] - a[1], 2));
                for (s = carry; s < seg; s += step) {
                    consider([a[0] + (c[0] - a[0]) * s / seg, a[1] + (c[1] - a[1]) * s / seg]);
                }
                carry = s - seg;
            }
        });
        return _.sortBy(advances, function(a) { return -a.km; });
    }

    /**
     * @returns {Object} how a fire changed from its earlier perimeter(s): {status: "both", feature:, old:,
     *          advances:, oldArea:, newArea:, since: (the earlier snapshot's time)}
     */
    function fireChange(feature, old, since) {
        return {
            status: "both", feature: feature, old: old, advances: fireAdvances(feature, old), since: since,
            oldArea: old.reduce(function(sum, f) { return sum + f.properties.area_km2; }, 0),
            newArea: feature.properties.area_km2
        };
    }

    /**
     * Compares two FEDS snapshots. Fires are matched by FEDS id, else by overlap (FEDS merges and splits fires).
     * FEDS's large-fire collection leaves a fire out of snapshots in which it had no detections, so the index's
     * fire ids decide what the rest are: a later fire listed in an earlier snapshot is compared with its last
     * perimeter there ("back", until loadTransition does so), else it is "new"; an earlier fire listed again after
     * the later snapshot is "unlisted", else it is out.
     *
     * @returns {Object} {fires: [{status: "both", "back" or "new", feature:, ...}, ...], out: [earlier features],
     *          unlisted: [earlier features]}
     */
    function buildTransition(before, after, index, from, to) {
        var byId = _.groupBy(before.features, function(f) { return f.properties.id; }), used = [];
        var earlier = index.snapshots.filter(function(time) { return time < from; }).reverse();
        var later = index.snapshots.filter(function(time) { return time > to; });
        var fires = after.features.map(function(feature) {
            var id = feature.properties.id, old = byId[id] || before.features.filter(function(f) {
                return firesOverlap(f, feature);
            });
            used.push.apply(used, old);
            if (old.length > 0) return fireChange(feature, old, from);
            var since = _.find(earlier, function(time) { return (index.listed[time] || {})[id]; });
            return {status: since ? "back" : "new", feature: feature, old: [], advances: [], since: since};
        });
        var rest = _.groupBy(_.difference(before.features, used), function(f) {
            return _.some(later, function(time) { return (index.listed[time] || {})[f.properties.id]; }) ?
                "unlisted" : "out";
        });
        return {fires: fires, out: rest.out || [], unlisted: rest.unlisted || []};
    }

    /**
     * Loads the two FEDS snapshots the configuration's transition names and compares them, then compares each
     * fire missing from the earlier snapshot but listed before it with its last perimeter there. Null when the fire
     * areas show one day. The result always has the snapshot times, for the menu to step through.
     */
    function loadTransition(attr) {
        if (attr.areas === "off" || !attr.transition || attr.transition === "off") return null;
        var cancel = this.cancel;
        return snapshotIndex().then(function(index) {
            var pair = _.extend(resolveTransition(index.snapshots, attr), {times: index.snapshots});
            if (pair.error) return pair;
            return when.all([loadSnapshot(pair.from), loadSnapshot(pair.to)]).then(function(both) {
                if (cancel.requested) return null;
                var transition = _.extend(buildTransition(both[0], both[1], index, pair.from, pair.to), pair);
                var back = transition.fires.filter(function(f) { return f.status === "back"; });
                return when.all(_.uniq(_.pluck(back, "since")).map(loadSnapshot)).then(function() {
                    return when.all(back.map(function(fire) {
                        return loadSnapshot(fire.since).then(function(snapshot) {
                            var old = snapshot.features.filter(function(f) {
                                return f.properties.id === fire.feature.properties.id;
                            });
                            _.extend(fire, old.length ? fireChange(fire.feature, old, fire.since) : {status: "new"});
                        });
                    }));
                }).then(function() {
                    return cancel.requested ? null : transition;
                });
            }, function() {
                return _.extend(pair, {error: "Cannot load the FEDS snapshots."});
            });
        });
    }

    /**
     * @returns {Boolean} true if [λ, φ] lies in the (Multi)Polygon: an odd number of ring crossings, holes included.
     *          Plain lon/lat geometry, which is close enough for fires a few km across.
     */
    function polygonContains(geometry, λ, φ) {
        var polygons = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates, inside = false;
        polygons.forEach(function(rings) {
            rings.forEach(function(ring) {
                for (var i = 0, j = ring.length - 1; i < ring.length; j = i++) {
                    var a = ring[i], b = ring[j];
                    if ((a[1] > φ) !== (b[1] > φ) && λ < (b[0] - a[0]) * (φ - a[1]) / (b[1] - a[1]) + a[0]) {
                        inside = !inside;
                    }
                }
            });
        });
        return inside;
    }

    /**
     * @returns {String} what a FEDS fire is called: its NIFC incident name, else its county, else its FEDS id.
     */
    function fireName(properties) {
        return properties.name ? properties.name + " Fire" : properties.place || "Fire " + properties.id;
    }

    /**
     * Labels the fires drawn, largest first, skipping labels that would overlap one already drawn or repeat its
     * name (FEDS can split one incident into several fires). The navigator's fires come first. Each label sits
     * just under its fire.
     *
     * @param {Array} drawn [{feature:, x:, y:}], y being the bottom of the fire on the screen.
     * @param {Array} top the navigator's fires.
     */
    function drawFireLabels(ctx, drawn, top) {
        var taken = [], names = {};
        ctx.font = FIRE_LABEL_FONT;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        ctx.lineJoin = "round";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.75)";
        ctx.fillStyle = "#ffffff";
        _.sortBy(drawn, function(d) {
            return (_.contains(top, d.feature) ? -1e6 : 0) - d.feature.properties.area_km2;
        }).forEach(function(d) {
            var text = fireName(d.feature.properties);
            if (names[text]) return;
            var width = ctx.measureText(text).width, top = d.y + 3;
            var box = [d.x - width / 2 - FIRE_LABEL_GAP, top - FIRE_LABEL_GAP,
                       d.x + width / 2 + FIRE_LABEL_GAP, top + 16 + FIRE_LABEL_GAP];
            var overlaps = taken.some(function(t) {
                return box[0] < t[2] && t[0] < box[2] && box[1] < t[3] && t[1] < box[3];
            });
            if (overlaps) return;
            taken.push(box);
            names[text] = true;
            ctx.strokeText(text, d.x, top);
            ctx.fillText(text, d.x, top);
        });
    }

    /**
     * @returns {Array} the fire's [x, y] center on the screen, or null if it is out of view (with a margin).
     */
    function fireOnScreen(feature, projection, isVisible) {
        var b = feature.bbox, λ = (b[0][0] + b[1][0]) / 2, φ = (b[0][1] + b[1][1]) / 2, c, margin = 50;
        if (!(c = projection([λ, φ])) || !isVisible(λ, φ) || c[0] < -margin || c[0] > view.width + margin ||
                c[1] < -margin || c[1] > view.height + margin) {
            return null;
        }
        return c;
    }

    /**
     * Adds the fire's rings to the canvas path as projected straight lines.
     * @returns {Array} [top, bottom] of the fire on the screen.
     */
    function traceFire(ctx, projection, feature) {
        var geometry = feature.geometry, top = Infinity, bottom = -Infinity;
        (geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates).forEach(function(rings) {
            rings.forEach(function(ring) {
                ring.forEach(function(coord, i) {
                    var p = projection(coord);
                    if (!p) return;
                    if (i === 0) ctx.moveTo(p[0], p[1]); else ctx.lineTo(p[0], p[1]);
                    top = Math.min(top, p[1]);
                    bottom = Math.max(bottom, p[1]);
                });
                ctx.closePath();
            });
        });
        return [top, bottom];
    }

    /**
     * Draws the FEDS fire perimeters, under the hotspots, and labels them. Fires are a few km across, so each is
     * drawn as projected straight lines if its center is in view, which is much cheaper on every frame of a drag
     * than d3.geo.path's clipping and resampling. Fires smaller than FIRE_AREA_MIN_SIZE on the screen are left to
     * their hotspots, though the navigator's fires are labeled at any size. In transition mode the change between
     * two snapshots is drawn instead.
     */
    function drawFireAreas(ctx, globe) {
        var areas = areaAgent.value();
        if (configuration.get("areas") === "off") return;
        if (transitionShown()) return drawTransition(ctx, globe, transitionAgent.value());
        if (!areas) return;
        var projection = globe.projection, isVisible = visibilityTest(globe);
        var pxPerDegree = projection.scale() * Math.PI / 180, drawn = [], top = largestFires(areas);
        ctx.beginPath();
        areas.features.forEach(function(feature) {
            var b = feature.bbox, c = fireOnScreen(feature, projection, isVisible);
            if (!c) return;
            if ((b[1][1] - b[0][1]) * pxPerDegree < FIRE_AREA_MIN_SIZE) {
                if (_.contains(top, feature)) drawn.push({feature: feature, x: c[0], y: c[1] + FIRE_POINT_SIZE});
                return;
            }
            drawn.push({feature: feature, x: c[0], y: Math.max(c[1], traceFire(ctx, projection, feature)[1])});
        });
        ctx.fillStyle = FIRE_AREA_FILL;
        ctx.fill("evenodd");  // holes in a perimeter stay unfilled
        ctx.lineWidth = FIRE_AREA_LINE_WIDTH;
        ctx.strokeStyle = FIRE_AREA_STROKE;
        ctx.stroke();
        drawFireLabels(ctx, drawn, top);
    }

    /**
     * @returns {Boolean} true if the fire areas show a transition between two snapshots, and it is loaded.
     */
    function transitionShown() {
        var t = transitionAgent.value();
        return !!(t && t.fires) && configuration.get("areas") !== "off" && configuration.get("transition") !== "off";
    }

    /**
     * @returns {Array} the arrows to draw for a fire at this zoom: its advances, largest first, whose tips are at
     *          least TRANSITION_ARROW_SPACING apart on the screen (and tails half that, so a corner of the earlier
     *          perimeter sends out few arrows) and that are at least TRANSITION_ARROW_MIN_LENGTH long there, at most
     *          TRANSITION_ARROWS_PER_FIRE. Kept on the fire until the zoom changes.
     */
    function arrowsAtScale(fire, scale) {
        if (fire.shown && fire.shown.scale === scale) return fire.shown.arrows;
        var pxPerKm = scale / 6371, spacing = TRANSITION_ARROW_SPACING / pxPerKm, arrows = [];
        for (var i = 0; i < fire.advances.length && arrows.length < TRANSITION_ARROWS_PER_FIRE; i++) {
            var a = fire.advances[i];
            if (a.km * pxPerKm < TRANSITION_ARROW_MIN_LENGTH) break;  // the rest are shorter still
            var apart = arrows.every(function(b) {
                return Math.sqrt(Math.pow(a.tip[0] - b.tip[0], 2) + Math.pow(a.tip[1] - b.tip[1], 2)) >= spacing &&
                    Math.sqrt(Math.pow(a.tail[0] - b.tail[0], 2) + Math.pow(a.tail[1] - b.tail[1], 2)) >= spacing / 2;
            });
            if (apart) arrows.push(a);
        }
        fire.shown = {scale: scale, arrows: arrows};
        return arrows;
    }

    /**
     * Draws a fire-area transition: the earlier perimeters faint and dashed, the later ones as usual, fires new in
     * the later snapshot in yellow and fires gone from it in dashed gray, each tagged, and white arrows from the
     * earlier perimeter out to the later one where a fire advanced.
     */
    function drawTransition(ctx, globe, transition) {
        var projection = globe.projection, isVisible = visibilityTest(globe), scale = projection.scale();
        var pxPerDegree = scale * Math.PI / 180, drawn = [], tags = [], arrows = [];
        var big = function(feature) {
            return (feature.bbox[1][1] - feature.bbox[0][1]) * pxPerDegree >= FIRE_AREA_MIN_SIZE;
        };
        var layer = function(fill, stroke, dash, features) {
            ctx.beginPath();
            var extents = features.map(function(feature) { return traceFire(ctx, projection, feature); });
            if (fill) {
                ctx.fillStyle = fill;
                ctx.fill("evenodd");
            }
            ctx.setLineDash(dash);
            ctx.lineWidth = FIRE_AREA_LINE_WIDTH;
            ctx.strokeStyle = stroke;
            ctx.stroke();
            ctx.setLineDash([]);
            return extents;
        };
        var old = [], grown = [], born = [], gone = [];
        transition.fires.forEach(function(fire) {
            var c = fireOnScreen(fire.feature, projection, isVisible);
            if (!c || !big(fire.feature)) return;
            if (fire.status === "new") {
                born.push(fire.feature);
                tags.push({x: c[0], feature: fire.feature, text: "new", color: TRANSITION_NEW_FIRE});
            }
            else {
                old.push.apply(old, fire.old);
                grown.push(fire.feature);
                arrows.push.apply(arrows, arrowsAtScale(fire, scale));
            }
            drawn.push({feature: fire.feature, x: c[0]});
        });
        var unlisted = [];
        transition.out.concat(transition.unlisted).forEach(function(feature) {
            var c = fireOnScreen(feature, projection, isVisible);
            if (!c || !big(feature)) return;
            if (_.contains(transition.unlisted, feature)) return unlisted.push(feature);
            gone.push(feature);
            tags.push({x: c[0], feature: feature, text: "out", color: TRANSITION_OUT_FIRE});
        });
        layer(TRANSITION_OLD_FILL, TRANSITION_OLD_STROKE, [4, 3], old);
        var bottoms = {};
        layer(FIRE_AREA_FILL, FIRE_AREA_STROKE, [], grown).forEach(function(e, i) {
            bottoms[grown[i].properties.id] = e[1];
        });
        layer("rgba(255, 225, 40, 0.15)", TRANSITION_NEW_FIRE, [], born).forEach(function(e, i) {
            bottoms[born[i].properties.id] = e[1];
        });
        layer(null, TRANSITION_OUT_FIRE, [5, 4], gone);
        layer(null, TRANSITION_UNLISTED_FIRE, [2, 4], unlisted);

        // Arrows: a dark halo under each, then the line and its head.
        var pxPerKm = scale / 6371;
        var trace = function() {
            ctx.beginPath();
            arrows.forEach(function(a) {
                var p = projection(a.from), q = projection(a.to);
                if (!p || !q) return;
                var dx = q[0] - p[0], dy = q[1] - p[1], length = Math.sqrt(dx * dx + dy * dy) || 1;
                var head = Math.min(9, Math.max(5, a.km * pxPerKm * 0.35)), ux = dx / length, uy = dy / length;
                ctx.moveTo(p[0], p[1]);
                ctx.lineTo(q[0], q[1]);
                ctx.moveTo(q[0] - head * (ux - uy * 0.5), q[1] - head * (uy + ux * 0.5));
                ctx.lineTo(q[0], q[1]);
                ctx.lineTo(q[0] - head * (ux + uy * 0.5), q[1] - head * (uy - ux * 0.5));
            });
        };
        ctx.lineCap = "round";
        ctx.lineJoin = "round";
        trace();
        ctx.lineWidth = 4;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.6)";
        ctx.stroke();
        ctx.lineWidth = 1.75;
        ctx.strokeStyle = TRANSITION_ARROW;
        ctx.stroke();
        ctx.lineCap = "butt";

        drawn.forEach(function(d) { d.y = bottoms[d.feature.properties.id]; });
        drawFireLabels(ctx, drawn.filter(function(d) { return _.isFinite(d.y); }), []);
        // Tags above the new and gone fires.
        ctx.font = FIRE_LABEL_FONT;
        ctx.textAlign = "center";
        ctx.textBaseline = "bottom";
        ctx.lineWidth = 3;
        ctx.strokeStyle = "rgba(0, 0, 0, 0.75)";
        tags.forEach(function(tag) {
            var y = traceTop(projection, tag.feature);
            if (!_.isFinite(y)) return;
            var text = tag.text === "out" ? "out: " + fireName(tag.feature.properties) : tag.text;
            ctx.fillStyle = tag.color;
            ctx.strokeText(text, tag.x, y - 3);
            ctx.fillText(text, tag.x, y - 3);
        });
    }

    /**
     * @returns {Number} the top of the fire on the screen.
     */
    function traceTop(projection, feature) {
        var b = feature.bbox, p = projection([(b[0][0] + b[1][0]) / 2, b[1][1]]);
        return p ? p[1] : NaN;
    }

    /**
     * Draws the fire perimeters, then the hotspots as square points colored by FRP, one path per FRP class, hottest
     * last. Zoomed in far enough, a point grows to the detection's pixel on the ground.
     */
    function drawFires() {
        var canvas = d3.select("#fires").node(), fires = fireAgent.value(), globe = globeAgent.value();
        µ.clearCanvas(canvas);
        if (!globe) return;
        var ctx = canvas.getContext("2d");
        drawFireAreas(ctx, globe);
        drawHeatFrame(ctx, globe);
        if (!fires || configuration.get("hotspots") === "off") return;

        var projection = globe.projection, isVisible = visibilityTest(globe), colors = fireColors();
        var pxPerKm = projection.scale() / 6371;  // scale is pixels per earth radius
        var paths = FIRE_FRP_CLASSES.map(function() { return []; });
        fires.detections.forEach(function(d) {
            var p = projection([d[0], d[1]]);
            if (!p || p[0] < 0 || p[0] > view.width || p[1] < 0 || p[1] > view.height || !isVisible(d[0], d[1])) {
                return;
            }
            paths[d[2]].push(p[0], p[1], Math.max(FIRE_POINT_SIZE, d[4] * pxPerKm));
        });
        paths.forEach(function(points, i) {
            if (points.length === 0) return;
            ctx.beginPath();
            for (var k = 0; k < points.length; k += 3) {
                var size = points[k + 2];
                ctx.rect(points[k] - size / 2, points[k + 1] - size / 2, size, size);
            }
            ctx.fillStyle = "rgb(" + colors[i].join(",") + ")";
            ctx.fill();
        });
    }

    /**
     * Loads the heat frames for the UTC day of the grids, made by scripts/heat_frames.py: for each fire, the heat
     * VIIRS saw at every overpass (detections from all three satellites plus the fire band), and each model run's
     * predicted heat at the overpasses after it was issued. A day with no file has no frames.
     */
    function loadHeatFrames(grids) {
        if (!grids) return null;
        var cancel = this.cancel;
        var date = configuration.get("date") === "current" ? "current" : grids.primaryGrid.date;
        return µ.loadJson(products.heatFramesPath(date)).then(function(data) {
            if (cancel.requested) return null;
            data.fires.forEach(function(fire) {
                fire.times = _.pluck(fire.observed, "time");
                var r = fire.radius_km / 111.32, c = fire.center;
                fire.bbox = [[c[0] - r / Math.cos(c[1] * Math.PI / 180), c[1] - r],
                             [c[0] + r / Math.cos(c[1] * Math.PI / 180), c[1] + r]];
            });
            heatFrame.index = 0;
            heatFrame.run = 0;
            return data;
        }).otherwise(function() {
            return null;
        });
    }

    // Which fire, overpass, and model run the heat frames show, and the timer while playing.
    var heatFrame = {fire: 0, index: 0, run: 0, timer: null};

    /**
     * @returns {Object} {fire:, observed:, run:, model:} what the heat frames show now, or null. model is the run's
     *          frame at the observed overpass, or undefined before the run's issue time.
     */
    function currentHeatFrame() {
        var data = framesAgent.value();
        if (!data || configuration.get("frames") !== "on" || !data.fires.length) return null;
        var fire = data.fires[Math.min(heatFrame.fire, data.fires.length - 1)];
        var observed = fire.observed[Math.min(heatFrame.index, fire.observed.length - 1)];
        var run = fire.runs[Math.min(heatFrame.run, fire.runs.length - 1)];
        var model = run && _.find(run.frames, function(f) { return f.time === observed.time; });
        return {fire: fire, observed: observed, run: run, model: model};
    }

    /**
     * Draws the current heat frame: observed cells filled, by FRP, as 375 m squares; the model run's cells that
     * VIIRS would detect outlined, colored by their predicted FRP, so a match reads as an outlined filled square.
     */
    function drawHeatFrame(ctx, globe) {
        var now = currentHeatFrame();
        if (!now) return;
        var projection = globe.projection, isVisible = visibilityTest(globe), colors = fireColors();
        var size = Math.max(FIRE_POINT_SIZE, now.fire.cell_m / 1000 * projection.scale() / 6371);
        var each = function(cells, keep, draw) {
            cells.forEach(function(c) {
                if (!keep(c) || !isVisible(c[0], c[1])) return;
                var p = projection([c[0], c[1]]);
                if (p && p[0] > -size && p[0] < view.width + size && p[1] > -size && p[1] < view.height + size) {
                    draw(p, c);
                }
            });
        };
        each(now.observed.cells, _.constant(true), function(p, c) {
            ctx.fillStyle = "rgb(" + colors[fireClass(c[2])].join(",") + ")";
            ctx.fillRect(p[0] - size / 2, p[1] - size / 2, size, size);
        });
        if (!now.model) return;
        ctx.lineWidth = Math.max(1, Math.min(2, size / 6));
        each(now.model.cells, function(c) { return c[3] === 1; }, function(p) {
            ctx.strokeStyle = FRAME_MODEL_STROKE;
            ctx.strokeRect(p[0] - size / 2, p[1] - size / 2, size, size);
        });
    }

    /**
     * Steps the heat frames to overpass i (wrapping) and redraws.
     */
    function showHeatFrameAt(i) {
        var now = currentHeatFrame();
        if (!now) return;
        var n = now.fire.observed.length;
        heatFrame.index = µ.floorMod(i, n);
        showHeatFrameDetails();
        drawFires();
    }

    function stopHeatFrames() {
        if (heatFrame.timer) {
            clearInterval(heatFrame.timer);
            heatFrame.timer = null;
        }
    }

    /**
     * Shows the heat frames in the info panel: the fire, the overpass with step and play buttons, what each source
     * saw, the model runs to pick from with their scores, and the legend.
     */
    function showHeatFrameDetails() {
        var section = d3.select("#info-frames");
        µ.removeChildren(section.node());
        if (configuration.get("frames") !== "on") return;
        section.append("p").attr("class", "info-title").text("Heat frames");
        var now = currentHeatFrame();
        if (!now) {
            stopHeatFrames();
            section.append("p").text("None for this day (scripts/heat_frames.py makes them)");
            return;
        }
        var fire = now.fire, o = now.observed, feds = fedsFireAt(fire.center);
        var title = section.append("p");
        title.append("span").attr("class", "text-button").attr("title", "Go to this fire")
            .text(feds ? fireName(feds.properties) : µ.formatCoordinates(fire.center[0], fire.center[1]))
            .on("click", function() { goToFire(feds || {bbox: fire.bbox}); });
        title.append("span").attr("class", "info-source").text("  model runs issued " + fire.issue.replace("T", " "));

        var nav = section.append("p");
        nav.append("span").attr("class", "text-button").attr("title", "Previous overpass").text("◀")
            .on("click", function() { stopHeatFrames(); showHeatFrameAt(heatFrame.index - 1); });
        nav.append("span").text(" " + (heatFrame.index + 1) + "/" + fire.observed.length + "  " +
            o.time.replace("T", " ") + " " + o.satellite + " ");
        nav.append("span").attr("class", "text-button").attr("title", "Next overpass").text("▶")
            .on("click", function() { stopHeatFrames(); showHeatFrameAt(heatFrame.index + 1); });
        nav.append("span").text("  ");
        nav.append("span").attr("class", "text-button").attr("title", "Play the overpasses in turn")
            .text(heatFrame.timer ? "Pause" : "Play").on("click", function() {
                if (heatFrame.timer) {
                    stopHeatFrames();
                    showHeatFrameDetails();
                    return;
                }
                heatFrame.timer = setInterval(function() { showHeatFrameAt(heatFrame.index + 1); }, FRAME_PLAY_MS);
                showHeatFrameAt(heatFrame.index + 1);
            });

        var legend = section.append("p");
        swatch(legend, fireColors()[3]);
        legend.append("span").text("observed: " + o.detections + " detections" +
            (o.fire_band_cells ? " + " + o.fire_band_cells + " fire band only" : "") + ", " +
            d3.format(",.0f")(o.frp_mw) + " MW");
        if (now.run) {
            var line = section.append("p");
            line.append("span").style("color", FRAME_MODEL_STROKE).text("□ ");
            var s = now.model && _.findWhere(now.run.scores, {time: o.time});
            line.append("span").text(s ?
                "model: " + s.predicted + " predicted detections, " +
                d3.format(",.0f")(s.frp_model_mw) + " MW; " + s.hits + " of " + (s.hits + s.misses) +
                " observed cells matched, CSI " + s.csi.toFixed(2) :
                "model: before the issue time, observed only");
            var runs = section.append("p");
            runs.append("span").text(fire.runs.length > FRAME_RUNS_SHOWN ?
                "Best " + FRAME_RUNS_SHOWN + " of " + fire.runs.length + " runs: " : "Runs: ");
            var shown = Math.min(fire.runs.length, FRAME_RUNS_SHOWN);
            fire.runs.slice(0, shown).forEach(function(run, i) {  // best first, as heat_frames.py sorts them
                var st = run.settings, label = st.head_ros_m_s === undefined ? run.name :
                    (st.head_ros_m_s ? st.head_ros_m_s + " m/s" : "no growth") +
                    (st.hpa_kj_m2 && st.head_ros_m_s ? " " + st.hpa_kj_m2 / 1000 + " MJ/m²" : "");
                runs.append("span").attr("class", "text-button" + (i === heatFrame.run ? " highlighted" : ""))
                    .attr("title", (run.settings.model || run.name) + "; mean CSI over the overpasses, then IoU " +
                        "with the next FEDS perimeter").text(label)
                    .on("click", function() { heatFrame.run = i; showHeatFrameDetails(); drawFires(); });
                var next = run.perimeters[0];
                runs.append("span").attr("class", "info-source").text(" " + (run.mean_csi || 0).toFixed(2) +
                    (next ? "/" + next.iou.toFixed(2) : "") + (i < shown - 1 ? "   " : ""));
            });
        }
        section.append("p").attr("class", "info-source").text(
            "Squares are 375 m cells. Filled: heat VIIRS saw at the overpass (" + o.sources.join(" + ") +
            "), by FRP. Outlined: cells the model run's heat would make VIIRS detect then. CSI = hits / (hits + " +
            "misses + false alarms), one cell of offset allowed.");
    }

    /**
     * @returns {Object} the FEDS fire whose perimeter holds the point, if the fire areas are loaded.
     */
    function fedsFireAt(point) {
        var areas = areaAgent.value();
        return areas && _.find(areas.features, function(feature) {
            var b = feature.bbox;
            return point[0] >= b[0][0] && point[0] <= b[1][0] && point[1] >= b[0][1] && point[1] <= b[1][1] &&
                polygonContains(feature.geometry, point[0], point[1]);
        });
    }

    /**
     * Shows the hotspots in the info panel: counts per satellite, the time window, and the color of each FRP class.
     */
    function showFireDetails(fires) {
        var section = d3.select("#info-fires");
        µ.removeChildren(section.node());
        if (configuration.get("hotspots") !== "off") {
            section.append("p").attr("class", "info-title").text("VIIRS Hotspots");
            if (!fires) {
                section.append("p").text("None for this day");
            }
            else {
                section.append("p").text(fires.detections.length + " detections: " + FIRE_SENSORS.map(function(sensor) {
                    return sensor.name + " " + (fires.sensors[sensor.code] || []).length;
                }).join(", "));
                section.append("p").text(fires.start + " to " + fires.end);
                var legend = section.append("p");
                fireColors().forEach(function(rgb, i) {
                    var last = i === FIRE_FRP_CLASSES.length - 1;
                    swatch(legend, rgb);
                    legend.append("span").text(FIRE_FRP_CLASSES[i] + (last ? "+ MW" : "  "));
                });
                section.append("p").attr("class", "info-source")
                    .text("One point per 375 m pixel, by fire radiative power (MW). NASA FIRMS");
            }
        }
    }

    /**
     * Shows the fire perimeters in the info panel: how many, and when FEDS made them.
     */
    function showAreaDetails(areas) {
        var section = d3.select("#info-areas");
        µ.removeChildren(section.node());
        if (configuration.get("areas") === "off") return;
        section.append("p").attr("class", "info-title").text("Fire areas");
        if (!areas) {
            section.append("p").text("None for this day");
            showTransitionControls(section);  // snapshots are listed apart from the day's file
            return;
        }
        var legend = section.append("p");
        legend.append("span").style("color", FIRE_AREA_STROKE).text("□ ");
        legend.append("span").text(areas.features.length + " active fires, as of " +
            areas.time.replace(":00:00", ":00Z").replace("T", " "));
        section.append("p").attr("class", "info-source").text(
            "Perimeters NASA FEDS draws around VIIRS detections it groups into fires, named after NIFC incidents " +
            "or else their county. Click a fire for its size.");
        showTransitionControls(section);
        showFireNavigator(section, areas);
    }

    var COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];

    /**
     * Shows the fire-area transition choices in the layers panel: Off, Last 12 h (the snapshot before the one at
     * the page's time), or Pick, which steps the two snapshots compared; then what changed between them, and the
     * legend.
     */
    function showTransitionControls(section) {
        var mode = configuration.get("transition") || "off", t = transitionAgent.value();
        var picked = mode !== "off" && mode !== "prev", times = (t && t.times) || [];
        var row = section.append("p"), button = function(label, title, on, click) {
            row.append("span").attr("class", "text-button" + (on ? " highlighted" : "")).attr("title", title)
                .text(label).on("click", click);
        };
        row.append("span").text("Change | ");
        button("Off", "Show the day's perimeters", mode === "off", function() {
            configuration.save({transition: "off"});
        });
        row.append("span").text(" – ");
        button("Last 12 h", "Arrows from the previous FEDS snapshot to the one at this time", mode === "prev",
            function() { configuration.save({transition: "prev"}); });
        row.append("span").text(" – ");
        button("Pick", "Choose the two FEDS snapshots to compare", picked, function() {
            if (picked) return;
            var from = t && t.from, to = t && t.to;
            if (!from || !to) {
                if (times.length < 2) return;
                from = times[times.length - 2];
                to = times[times.length - 1];
            }
            configuration.save({transition: snapshotKey(from) + "," + snapshotKey(to)});
        });
        if (mode === "off" || !t) return;

        if (picked && times.length > 1) {
            var keys = mode.split(","), at = keys.map(function(key) { return times.map(snapshotKey).indexOf(key); });
            var nav = section.append("p"), stepper = function(which, label) {
                var i = at[which], other = at[1 - which];
                var go = function(j, glyph) {
                    var ok = j >= 0 && j < times.length && (which === 0 ? other < 0 || j < other : j > other);
                    nav.append("span").attr("class", "text-button" + (ok ? "" : " disabled"))
                        .attr("title", (glyph === "◀" ? "Earlier" : "Later") + " snapshot").text(glyph)
                        .on("click", function() {
                            if (!ok) return;
                            var next = keys.slice();
                            next[which] = snapshotKey(times[j]);
                            configuration.save({transition: next.join(",")});
                        });
                };
                nav.append("span").text(label + " ");
                go(i < 0 ? times.length - 1 : i - 1, "◀");
                nav.append("span").text(" " + (i < 0 ? keys[which].replace("T", " ") + "Z" :
                    snapshotLabel(times[i])) + " ");
                go(i < 0 ? 0 : i + 1, "▶");
            };
            stepper(0, "From");
            nav.append("span").text("   ");
            stepper(1, "to");
        }
        if (t.error) {
            section.append("p").text(t.error);
            return;
        }
        var both = t.fires.filter(function(f) { return f.status === "both"; });
        var grew = both.filter(function(f) { return f.advances.length > 0; }).length;
        var hours = Math.round((snapshotMillis(t.to) - snapshotMillis(t.from)) / HOUR);
        var resumed = both.filter(function(f) { return f.since !== t.from; }).length;
        section.append("p").text(snapshotLabel(t.from) + " → " + snapshotLabel(t.to) + " (" + hours + " h): " +
            both.length + " fires compared" + (resumed ? " (" + resumed + " with an earlier snapshot)" : "") +
            ", " + grew + " advanced; " + (t.fires.length - both.length) + " new, " + t.out.length + " out" +
            (t.unlisted.length ? ", " + t.unlisted.length + " not listed at " + snapshotLabel(t.to) : ""));
        var legend = section.append("p");
        legend.append("span").style("color", TRANSITION_OLD_STROKE).text("┅ ");
        legend.append("span").text(snapshotLabel(t.from) + "  ");
        legend.append("span").style("color", FIRE_AREA_STROKE).text("□ ");
        legend.append("span").text(snapshotLabel(t.to) + "  ");
        legend.append("span").style("color", TRANSITION_ARROW).text("→ ");
        legend.append("span").text("advance  ");
        legend.append("span").style("color", TRANSITION_NEW_FIRE).text("□ ");
        legend.append("span").text("new  ");
        legend.append("span").style("color", TRANSITION_OUT_FIRE).text("┅ ");
        legend.append("span").text("out  ");
        legend.append("span").style("color", TRANSITION_UNLISTED_FIRE).text("┈ ");
        legend.append("span").text("not listed");
        section.append("p").attr("class", "info-source").text(
            "Arrows run from the nearest point of the earlier perimeter to points of the later one beyond it, " +
            "for advances over " + TRANSITION_MIN_ADVANCE_KM * 1000 + " m (a VIIRS pixel), largest first, fewer " +
            "when zoomed out. Fires are matched by FEDS id, else by overlap. FEDS leaves out of a snapshot " +
            "fires it saw no detections in, so a fire missing from the earlier snapshot is compared with its last " +
            "listed perimeter, and is new only if never listed before; one missing from the later snapshot is " +
            "out only if not listed again in the snapshots after it. Click a fire for its growth.");
    }

    /**
     * @returns {Object} the transition's fire at [λ, φ]: one whose later or earlier perimeter holds it, or an
     *          {status: "out", feature:} gone from the later snapshot.
     */
    function transitionFireAt(λ, φ) {
        var t = transitionAgent.value(), holds = function(feature) {
            var b = feature.bbox;
            return λ >= b[0][0] && λ <= b[1][0] && φ >= b[0][1] && φ <= b[1][1] &&
                polygonContains(feature.geometry, λ, φ);
        };
        var fire = _.find(t.fires, function(f) { return holds(f.feature); }) ||
            _.find(t.fires, function(f) { return f.old.some(holds); });
        var out = !fire && _.find(t.out, holds), unlisted = !fire && !out && _.find(t.unlisted, holds);
        return fire || (out && {status: "out", feature: out}) || (unlisted && {status: "unlisted", feature: unlisted});
    }

    /**
     * @returns {Object} the transition's fire with that FEDS id, later or earlier, as for transitionFireAt.
     */
    function transitionFireById(id) {
        var t = transitionAgent.value(), same = function(f) { return f.properties.id === id; };
        var fire = _.find(t.fires, function(f) { return same(f.feature); }) ||
            _.find(t.fires, function(f) { return f.old.some(same); });
        var out = !fire && _.find(t.out, same), unlisted = !fire && !out && _.find(t.unlisted, same);
        return fire || (out && {status: "out", feature: out}) || (unlisted && {status: "unlisted", feature: unlisted});
    }

    /**
     * Shows how a fire changed between the transition's snapshots in the location details: growth in km² and %,
     * and its largest advance and the direction of it; or that it is new, or out.
     */
    function describeTransitionFire(fire) {
        var t = transitionAgent.value(), p = fire.feature.properties, element = d3.select("#location-area");
        var km2 = d3.format(",.1f");
        element.text("");
        element.append("span").text(fireName(p) + (p.name ? ", " + p.place : "") + " (FEDS " + p.id + ")");
        element.append("br");
        if (fire.status === "new") {
            element.append("span").text("New: not in the " + snapshotLabel(t.from) + " snapshot; " + km2(p.area_km2) +
                " km² at " + snapshotLabel(t.to));
            return;
        }
        if (fire.status === "unlisted") {
            element.append("span").text("Not in the " + snapshotLabel(t.to) + " snapshot (no detections then?), " +
                "but listed again later; " + km2(p.area_km2) + " km² at " + snapshotLabel(t.from));
            return;
        }
        if (fire.status === "out") {
            element.append("span").text("Out: " + km2(p.area_km2) + " km² at " + snapshotLabel(t.from) +
                ", not in the " + snapshotLabel(t.to) + " snapshot");
            return;
        }
        var growth = fire.newArea - fire.oldArea, sign = growth >= 0 ? "+" : "−";
        element.append("span").text(km2(fire.oldArea) + " → " + km2(fire.newArea) + " km², " + sign +
            km2(Math.abs(growth)) + " km² (" + sign + d3.format(",.0f")(Math.abs(growth) / fire.oldArea * 100) +
            "%), " + snapshotLabel(fire.since) + " → " + snapshotLabel(t.to));
        element.append("br");
        var a = fire.advances[0];
        element.append("span").text(a ?
            "Largest advance " + d3.format(",.1f")(a.km) + " km toward " + COMPASS[Math.round(a.bearing / 22.5) % 16] +
                " (" + Math.round(a.bearing) + "°)" :
            "No advance over " + TRANSITION_MIN_ADVANCE_KM * 1000 + " m");
    }

    /**
     * @returns {Array} the FIRE_NAVIGATOR_COUNT largest fires burning under FIRE_NAVIGATOR_MAX_DAYS, one per name,
     *          largest first. Kept on the areas, as they are labeled on every frame.
     */
    function largestFires(areas) {
        if (!areas.largest) {
            var seen = {};
            areas.largest = _.sortBy(areas.features.filter(function(feature) {
                return feature.properties.days <= FIRE_NAVIGATOR_MAX_DAYS;
            }), function(feature) { return -feature.properties.area_km2; }).filter(function(feature) {
                var name = fireName(feature.properties);
                return !seen[name] && (seen[name] = true);
            }).slice(0, FIRE_NAVIGATOR_COUNT);
        }
        return areas.largest;
    }

    /**
     * Lists the largest fires burning under FIRE_NAVIGATOR_MAX_DAYS, one per name. Clicking one turns the globe to
     * it and zooms in until it fills FIRE_NAVIGATOR_SHARE of the view.
     */
    function showFireNavigator(section, areas) {
        var fires = largestFires(areas);
        if (fires.length === 0) return;
        section.append("p").text("Largest fires burning under " + FIRE_NAVIGATOR_MAX_DAYS + " days:");
        fires.forEach(function(feature, i) {
            var p = feature.properties, row = section.append("p").attr("class", "fire-navigator");
            row.append("span").text((i + 1) + ". ");
            row.append("span").attr("class", "text-button").attr("title", "Go to this fire").text(fireName(p))
                .on("click", function() { goToFire(feature); });
            row.append("span").attr("class", "info-source").text("  " + d3.format(",.1f")(p.area_km2) + " km², " +
                d3.format(",.0f")(p.days) + (p.days === 1 ? " day" : " days"));
        });
    }

    /**
     * @returns {Object} {x:, y:, size:} the middle and smaller side of the open screen space a fire gone to is shown
     *          in: above both menus, or above the menu on the left and beside the layers panel, whichever is roomier.
     */
    function openSpotOnScreen() {
        var rect = function(id) {
            var r = d3.select(id).node().getBoundingClientRect();
            return r.height > 0 ? r : {top: view.height, left: view.width};
        };
        var menu = rect("#details"), layers = rect("#layers-panel");
        var spaces = [
            {width: view.width, height: Math.min(menu.top, layers.top)},
            {width: layers.left, height: menu.top}
        ];
        var best = _.max(spaces, function(space) { return Math.min(space.width, space.height); });
        return {x: best.width / 2, y: best.height / 2, size: Math.max(Math.min(best.width, best.height), 100)};
    }

    /**
     * Turns the globe to a fire and zooms in on it, in the open space above the menus, then shows the fire's
     * details in place of a clicked location's.
     */
    function goToFire(feature) {
        var globe = globeAgent.value();
        if (!globe) return;
        var b = feature.bbox, λ = (b[0][0] + b[1][0]) / 2, φ = (b[0][1] + b[1][1]) / 2;
        var extent = Math.max((b[1][0] - b[0][0]) * Math.cos(φ * Math.PI / 180), b[1][1] - b[0][1], 0.01);
        var spot = openSpotOnScreen(), scale = FIRE_NAVIGATOR_SHARE * spot.size / (extent * Math.PI / 180);
        scale = µ.clamp(scale, globe.fit(view), FIRE_NAVIGATOR_MAX_SCALE);
        // Centered on the fire, the point as far past the center as the open spot is short of it puts the fire there.
        var center = globe.center(view);
        var saved = globe.orientation();
        globe.orientation([λ, φ, Math.round(scale)].join(","), view);
        var shifted = globe.projection.invert([2 * center[0] - spot.x, 2 * center[1] - spot.y]);
        globe.orientation(saved, view);
        if (shifted && _.isFinite(shifted[0]) && _.isFinite(shifted[1])) {
            λ = shifted[0];
            φ = shifted[1];
        }
        var digits = scale < 100000 ? 4 : 5;
        configuration.save({orientation: [λ.toFixed(digits), φ.toFixed(digits), Math.round(scale)].join(",")});
        λ = (b[0][0] + b[1][0]) / 2;
        φ = (b[0][1] + b[1][1]) / 2;
        clearLocationDetails(true);
        d3.select("#location-coord").text(µ.formatCoordinates(λ, φ));
        d3.select("#location-close").classed("invisible", false);
        if (feature.properties) describeFire(feature);
    }

    /**
     * Shows the FEDS fire at a clicked location, if any.
     */
    function showAreaAtLocation(point, coord) {
        var areas = areaAgent.value();
        d3.select("#location-area").text("");
        if (configuration.get("areas") === "off" || !coord || !_.isFinite(coord[0])) return;
        var λ = coord[0], φ = coord[1];
        if (transitionShown()) {
            var changed = transitionFireAt(λ, φ);
            if (changed) describeTransitionFire(changed);
            return;
        }
        if (!areas) return;
        var fire = _.find(areas.features, function(feature) {
            var b = feature.bbox;
            return λ >= b[0][0] && λ <= b[1][0] && φ >= b[0][1] && φ <= b[1][1] &&
                polygonContains(feature.geometry, λ, φ);
        });
        if (fire) describeFire(fire);
    }

    /**
     * Shows a FEDS fire's name, size, age and power in the location details.
     */
    function describeFire(fire) {
        var changed = transitionShown() && transitionFireById(fire.properties.id);
        if (changed) return describeTransitionFire(changed);
        var p = fire.properties, element = d3.select("#location-area");
        // Two lines, so the details box stays no wider than the menu under it.
        element.text("");
        element.append("span").text(fireName(p) + (p.name ? ", " + p.place : "") + " (FEDS " + p.id + ")");
        element.append("br");
        element.append("span").text(d3.format(",.1f")(p.area_km2) + " km² (" +
            d3.format(",.0f")(p.area_km2 * 247.105) + " acres), " + d3.format(",.1f")(p.days) + " days" +
            (_.isFinite(p.mean_frp) ? ", mean FRP " + p.mean_frp + " MW" : ""));
    }

    /**
     * Explains the smoke particles in the layers panel, when the particles trace smoke.
     */
    function showSmokeDetails(grids) {
        var section = d3.select("#info-smoke"), langCode = d3.select("body").attr("data-lang") || "en";
        µ.removeChildren(section.node());
        if (configuration.get("flow") !== "smoke" || !grids) return;
        var description = grids.primaryGrid.description(langCode);
        section.append("p").attr("class", "info-title").text("Smoke paths");
        section.append("p").text("Particles leave the hotspots, more from more powerful fires, and drift on the " +
            description.name + description.qualifier + ".");
        section.append("p").attr("class", "info-source").text(
            "Smoke usually rises to about 850-700 hPa (1.5-3 km); choose that under Height. Not a dispersion model: " +
            "no plume rise, mixing or deposition.");
    }

    /**
     * @returns {Object} an underlay of VIIRS I4 (3.7 µm) brightness temperature, the band VIIRS finds fires with, for
     *          NOAA-21's "Day" or "Night" pass over the lower 48, made from the raw swaths by scripts/viirs_i4_tiles.py.
     */
    function fireBandUnderlay(pass) {
        var dir = "/data/thermal/i4/{date}/N21-" + pass.toLowerCase();
        return {url: ["/tiles/relief/{z}/{x}/{y}", {url: dir + "/{z}/{x}/{y}.png", maxZoom: 9}],
            minZoom: 1, maxZoom: 16, base: 2, bounds: [-126, 23, -65, 51],
            thermal: true, pass: pass, scale: "/data/thermal/i4-scale.json", legend: [270, 365],
            identify: "/identify/i4?pass=N21-" + pass.toLowerCase(),
            info: dir + "/info.json",  // the swaths, written by viirs_i4_tiles.py
            note: pass === "Day" ?
                "Fires show as hot spots: this is the band FIRMS detects them in. By day the band also sees " +
                "reflected sunlight, so clear land reads warm and clouds can read warm too." :
                "Fires show as hot spots: this is the band FIRMS detects them in. At night there is no reflected " +
                "sunlight, so the ground reads cool and fires stand out most.",
            credit: "NASA LAADS: VIIRS I4 (3.7 µm) brightness temperature, 375 m, NOAA-21 " +
                (pass === "Day" ? "afternoon" : "night") + " pass"};
    }

    /**
     * @returns {Object} an underlay of VIIRS I5 brightness temperature for the "Day" or "Night" passes, over relief.
     *          dev-server.js recolors the GIBS tiles with /data/thermal/brightness-temp-scale.json.
     */
    function thermalUnderlay(pass) {
        // dev-server.js merges the satellites into one layer, as two translucent layers would darken their overlap.
        var thermal = {url: "/tiles/thermal/" + pass + "/{date}/{z}/{x}/{y}", maxZoom: 9};
        return {url: ["/tiles/relief/{z}/{x}/{y}", thermal], minZoom: 1, maxZoom: 16, base: 2,
            scale: "/data/thermal/brightness-temp-scale.json", legend: [240, 340],  // colder reads as cloud
            identify: "/identify/thermal?pass=" + pass,
            note: "Clouds read cold. Most fires fill a fraction of a pixel and warm it only a few degrees in this " +
                "band; hotspots are found with the 3.7 µm band (Fire band underlay).",
            thermal: true,
            pass: pass,
            credit: "NASA GIBS: VIIRS I5 (11.5 µm) brightness temperature, 375 m, NOAA-20 over S-NPP, " +
                (pass === "Day" ? "afternoon" : "night") + " passes"};
    }

    /**
     * @returns {String} the UTC day as "2026-10-28" of the hotspots seen on the underlay's pass ("Day" or "Night"),
     *          so the thermal mosaic shows the same overpasses; failing that, the day of the weather data.
     */
    function underlayDate(underlay) {
        var fires = fireAgent.value(), grids = gridAgent.value();
        if (fires && underlay && fires.passDates[underlay.pass]) return fires.passDates[underlay.pass];
        return grids ? µ.dateToUTCymd(grids.primaryGrid.date, "-") : null;
    }

    var MAP_LINES = "/tiles/hydro/{z}/{x}/{y}";  // the Map toggle's rivers, lakes and names, drawn over other underlays

    /**
     * @returns {String} the name of the tile source to draw: the chosen underlay, with the map's rivers, lakes and
     *          names on top if the Map toggle is on (made on first use, as "satellite+map" and so on), or "map" alone.
     */
    function underlaySourceName() {
        var name = configuration.get("underlay"), map = configuration.get("map") === "on";
        if (!UNDERLAYS[name] || name === "map") return map || name === "map" ? "map" : null;
        if (!map) return name;
        var key = name + "+map";
        if (!UNDERLAYS[key]) {
            UNDERLAYS[key] = _.extend({}, UNDERLAYS[name], {url: [].concat(UNDERLAYS[name].url, MAP_LINES)});
        }
        return key;
    }

    function drawUnderlay() {
        if (!tileUnderlay) return;
        var globe = globeAgent.value(), name = underlaySourceName();
        var date = underlayDate(UNDERLAYS[name]);
        tileUnderlay.setSource(name);
        tileUnderlay.setBounds(products.region(configuration.get("region")).bounds);
        if (date) tileUnderlay.setParams({date: date});
        if (!globe || !UNDERLAYS[name] || !tileUnderlay.supports(globe.projectionName) ||
                (UNDERLAYS[name].thermal && !date)) {
            tileUnderlay.clear();
            return;
        }
        tileUnderlay.draw(globe.projection);
    }

    var fuelColors = _.memoize(function() {
        return µ.loadJson("/data/fuels/fbfm40-colors.json").otherwise(function() { return {}; });
    });

    /**
     * @returns {Promise} f(kelvin) -> [r, g, b, alpha 0-1], the colors a thermal underlay's tiles are drawn in, from
     *          its scale file.
     */
    var thermalColors = _.memoize(function(scaleUrl) {
        return µ.loadJson(scaleUrl).then(function(scale) {
            var stops = scale.stops;
            return function(k) {
                var j = 1;
                while (j < stops.length - 1 && stops[j][0] < k) j++;
                var a = stops[j - 1], b = stops[j], t = µ.clamp((k - a[0]) / (b[0] - a[0]), 0, 1);
                return [1, 2, 3, 4].map(function(c) {
                    var v = a[c] + t * (b[c] - a[c]);
                    return c < 4 ? Math.round(v) : v;
                });
            };
        });
    });

    function kelvinToCelsius(k) {
        return k - 273.15;
    }

    function swatch(selection, rgb) {
        selection.append("span").style("color", rgb ? "rgb(" + rgb.join(",") + ")" : null).text("■ ");
    }

    /**
     * Shows the underlay's source in the menu, with a legend of fuel model groups for the fuel underlay.
     */
    var underlayDetailsShown = 0;  // counts calls, so a slow legend load from an earlier call is dropped

    function showUnderlayDetails() {
        var section = d3.select("#info-underlay"), name = configuration.get("underlay"), underlay = UNDERLAYS[name];
        var map = configuration.get("map") === "on";
        var call = ++underlayDetailsShown;
        µ.removeChildren(section.node());
        if (!underlay && map) {
            name = "map";
            underlay = UNDERLAYS.map;
        }
        if (underlay) {
            var note = canShowUnderlay(globeAgent.value()) ? "" : " (globe projection only)";
            var withMap = map && name !== "map" ? " + Map" : "";
            section.append("p").attr("class", "info-title").text(d3.select("#underlay-" + name).text() + withMap + " underlay");
            section.append("p").attr("class", "info-source").text(underlay.credit + note);
            if (withMap) {
                section.append("p").attr("class", "info-source").text("Map: USGS The National Map hydrography (rivers, lakes, names)");
            }
            if (underlay.thermal) {
                thermalColors(underlay.scale).then(function(color) {
                    if (call !== underlayDetailsShown) return;
                    var c0 = kelvinToCelsius(underlay.legend[0]), c1 = kelvinToCelsius(underlay.legend[1]);
                    var celsiusAt = function(t) { return µ.spread(t, c0, c1); };
                    section.select(".info-title").append("span").attr("class", "info-source").text("  °C");
                    appendScaleBar(section, function(t) { return color(celsiusAt(t) + 273.15); },
                        function(t) { return celsiusAt(t).toFixed(1) + " °C"; },
                        d3.scale.linear().domain([c0, c1]).ticks(5).map(function(v) {
                            return {at: (v - c0) / (c1 - c0), label: String(v)};
                        }));
                    var note = section.append("p").attr("class", "info-source").text(underlayDate(underlay) + ". " +
                        underlay.note + " Click the map for the temperature.");
                    if (underlay.info) {
                        // Which swaths the day's tiles come from, or that there are none for this day.
                        var hhmm = function(s) { return s.substr(0, 2) + ":" + s.substr(2); };
                        µ.loadJson(underlay.info.replace("{date}", underlayDate(underlay))).then(function(info) {
                            if (call !== underlayDetailsShown) return;
                            var end = +info.last_granule.substr(0, 2) * 60 + +info.last_granule.substr(2) + 6;
                            note.text(underlayDate(underlay) + ", swaths " + hhmm(info.first_granule) + " to " +
                                hhmm(µ.zeroPad(Math.floor(end / 60), 2) + µ.zeroPad(end % 60, 2)) + " UTC. " +
                                underlay.note + " Click the map for the temperature.");
                        }).otherwise(function() {
                            if (call !== underlayDetailsShown) return;
                            note.text("No fire-band pass downloaded for " + underlayDate(underlay) + " (see " +
                                "scripts/viirs_i4_tiles.py).");
                        });
                    }
                });
            }
            if (name === "fuel") {
                fuelColors().then(function(colors) {
                    if (call !== underlayDetailsShown) return;
                    var legend = section.append("p").attr("class", "info-columns");
                    _.values(FUEL_GROUPS).forEach(function(group) {
                        var item = legend.append("span");
                        swatch(item, colors[group.legend]);
                        item.append("span").text(group.name);
                    });
                    section.append("p").attr("class", "info-source").text("Click the map for the fuel model.");
                });
            }
        }
    }

    function canShowUnderlay(globe) {
        return !!tileUnderlay && !!globe && tileUnderlay.supports(globe.projectionName);
    }

    /**
     * @returns {String} e.g. "TL8 Timber litter" for FBFM40 code 188, or null for codes that are not fuel models.
     */
    function fuelModelName(code) {
        var group = FUEL_GROUPS[Math.floor(code / 10)], n = code % 10;
        if (!group || n === 0) return null;
        var label = group.prefix + n;
        return group.prefix === "NB" ? label + " " + (NON_BURNABLE[n] || group.name) : label + " " + group.name;
    }

    /**
     * Looks up the underlay's value at a clicked location: the LANDFIRE fuel model, or the VIIRS temperature.
     */
    function showUnderlayAtLocation(point, coord) {
        var element = d3.select("#location-underlay"), underlay = UNDERLAYS[configuration.get("underlay")];
        µ.removeChildren(element.node());
        if (!underlay || !coord || !_.isFinite(coord[0]) || !_.isFinite(coord[1])) return;
        var at = "lon=" + coord[0].toFixed(5) + "&lat=" + coord[1].toFixed(5);
        if (underlay.thermal) {
            var date = underlayDate(underlay);
            if (!date) return;
            var thermalUrl = underlay.identify + "&" + at + "&date=" + date;
            when.all([µ.loadJson(thermalUrl), thermalColors(underlay.scale)]).then(function(results) {
                var k = results[0].value;
                if (activeLocation.coord !== coord) return;
                µ.removeChildren(element.node());
                if (!_.isFinite(k)) return element.text("VIIRS no data here");
                swatch(element, results[1](k).slice(0, 3));
                element.append("span").text("VIIRS " + kelvinToCelsius(k).toFixed(1) + " °C (" + k + " K)");
            }).otherwise(function() {
                element.text("VIIRS lookup failed");
            });
            return;
        }
        if (configuration.get("underlay") !== "fuel") return;
        var url = "/identify/fuel?" + at;
        when.all([µ.loadJson(url), fuelColors()]).then(function(results) {
            var code = results[0].value, name = _.isFinite(code) ? fuelModelName(code) : null;
            if (activeLocation.coord !== coord) return;  // the user has clicked somewhere else since
            µ.removeChildren(element.node());
            if (name) swatch(element, results[1][name.split(" ")[0]]);
            element.append("span").text("Fuel " + (name || "none here"));
        }).otherwise(function() {
            element.text("Fuel lookup failed");
        });
    }

    function drawGridPoints(ctx, grid, globe) {
        if (!grid || !globe || !configuration.get("showGridPoints")) return;

        ctx.fillStyle = "rgba(255, 255, 255, 1)";
        // Use the clipping behavior of a projection stream to quickly draw visible points.
        var stream = globe.projection.stream({
            point: function(x, y) {
                ctx.fillRect(Math.round(x), Math.round(y), 1, 1);
            }
        });
        grid.forEachPoint(function(λ, φ, d) {
            if (µ.isValue(d)) {
                stream.point(λ, φ);
            }
        });
    }

    // Unit choices for overlay values shown in the info panel and location details, by product type.
    var overlayUnitIndex = {};

    function overlayUnits(grid) {
        return grid.units[(overlayUnitIndex[grid.type] || 0) % grid.units.length];
    }

    /**
     * @returns {Number} the value at fraction t along a color scale bar: linear, or logarithmic if the scale says so.
     */
    function scaleValueAt(scale, t) {
        var bounds = scale.bounds;
        return scale.logarithmic ?
            Math.pow(bounds[0], 1 - t) * Math.pow(bounds[1], t) :
            µ.spread(t, bounds[0], bounds[1]);
    }

    /**
     * @returns {Array} labeled ticks for a grid's color scale bar, as [{at: fraction along the bar, label:}], in the
     *          units currently chosen for it (shown in the overlay's title). Logarithmic scales get powers of ten, others a few round numbers.
     */
    function scaleTicks(grid) {
        var scale = grid.scale, bounds = scale.bounds, units = overlayUnits(grid), ticks;
        if (scale.logarithmic) {
            ticks = [];
            for (var v = Math.pow(10, Math.ceil(Math.log(bounds[0]) / Math.LN10)); v <= bounds[1]; v *= 10) {
                ticks.push({at: Math.log(v / bounds[0]) / Math.log(bounds[1] / bounds[0]), value: v});
            }
        }
        else {
            // Ticks are round in the chosen units, which are a linear conversion of the scale's own.
            var c0 = units.conversion(bounds[0]), c1 = units.conversion(bounds[1]);
            ticks = d3.scale.linear().domain([c0, c1]).ticks(4).map(function(v) {
                return {at: (v - c0) / (c1 - c0), value: v, converted: true};
            });
        }
        return ticks.filter(function(tick) { return tick.at >= 0 && tick.at <= 1; }).map(function(tick) {
            var number = tick.converted ? tick.value : units.conversion(tick.value);
            return {at: tick.at, label: d3.format(",")(+number.toPrecision(6))};
        });
    }

    /**
     * Appends a color scale bar to a layers panel section: colorAt(t) and labelAt(t) give the color and hover text at
     * fraction t along it, and ticks are [{at: fraction, label:}].
     */
    function appendScaleBar(section, colorAt, labelAt, ticks) {
        var colorBar = section.append("p").append("canvas").attr("class", "info-scale");
        var c = colorBar.node();
        c.width = c.offsetWidth;
        c.height = c.offsetHeight;
        var g = c.getContext("2d"), n = c.width - 1;
        for (var i = 0; i <= n; i++) {
            var rgb = colorAt(i / n);
            g.fillStyle = "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";
            g.fillRect(i, 0, 1, c.height);
        }
        colorBar.on("mousemove", function() {
            var x = d3.mouse(this)[0] * c.width / c.offsetWidth;
            colorBar.attr("title", labelAt(µ.clamp(x / n, 0, 1)));
        });
        var tickRow = section.append("p").attr("class", "info-ticks");
        ticks.forEach(function(tick) {
            // Centered on its tick, except at the ends, where it stays inside the bar.
            var shift = tick.at < 0.05 ? "0" : tick.at > 0.95 ? "-100%" : "-50%";
            tickRow.append("span").style("left", tick.at * 100 + "%").style("transform", "translateX(" + shift + ")")
                .text(tick.label);
        });
    }

    /**
     * Shows each overlay in the layers panel: its name, a color scale with labeled ticks (hover for any value),
     * and its source.
     */
    function showOverlayDetails(overlays) {
        var section = d3.select("#info-overlays"), langCode = d3.select("body").attr("data-lang") || "en";
        µ.removeChildren(section.node());
        (overlays ? overlays.list : []).forEach(function(grid) {
            var description = grid.description(langCode);
            var title = section.append("p").attr("class", "info-title").text(description.name + description.qualifier);
            title.append("span").attr("class", "info-source").text("  " + overlayUnits(grid).label);
            appendScaleBar(section, function(t) {
                return grid.scale.gradient(scaleValueAt(grid.scale, t), 1);
            }, function(t) {
                var units = overlayUnits(grid);
                return µ.formatScalar(scaleValueAt(grid.scale, t), units) + " " + units.label;
            }, scaleTicks(grid));
            section.append("p").attr("class", "info-source").text(grid.source);
        });
    }

    /**
     * Draws the overlays: on the GPU if the field allows it, otherwise on the CPU (asynchronously). Draws nothing
     * if hide is true, or if the overlays were loaded for other grids than the field's.
     */
    function drawOverlay(field, overlays, hide) {
        var canvas = d3.select("#overlay").node(), ctx = canvas.getContext("2d");
        µ.clearCanvas(canvas);
        if (gpuOverlay) {
            gpuOverlay.clear();
        }
        if (!field || !overlays || overlays.grids !== field.grids || hide) return;

        var list = overlays.list, pointsGrid = list[0] || field.grids.primaryGrid;
        if (list.length > 0 && !field.gpu) {
            return renderOverlayImage(field, list, (this || {}).cancel || {}).then(function(image) {
                if (image) {
                    ctx.putImageData(image, 0, 0);
                    drawGridPoints(ctx, pointsGrid, field.globe);
                }
            });
        }
        if (list.length > 0) {
            gpuOverlay.setGrids(list);
            gpuOverlay.draw(field.globe.projection);
        }
        drawGridPoints(ctx, pointsGrid, field.globe);
    }

    /**
     * Extract the date the grids are valid, or the current date if no grid is available.
     * UNDONE: if the grids hold unloaded products, then the date can be extracted from them.
     *         This function would simplify nicely.
     */
    function validityDate(grids) {
        // When the active layer is considered "current", use its time as now, otherwise use current time as
        // now (but rounded down to the nearest three-hour block).
        var THREE_HOURS = 3 * HOUR;
        var now = grids ? grids.primaryGrid.date.getTime() : Math.floor(Date.now() / THREE_HOURS) * THREE_HOURS;
        var parts = configuration.get("date").split("/");  // yyyy/mm/dd or "current"
        var hhmm = configuration.get("hour");
        return parts.length > 1 ?
            Date.UTC(+parts[0], parts[1] - 1, +parts[2], +hhmm.substring(0, 2)) :
            parts[0] === "current" ? now : null;
    }

    /**
     * Display the grid's validity date in the menu. Allow toggling between local and UTC time.
     */
    function showDate(grids) {
        var date = new Date(validityDate(grids)), isLocal = d3.select("#data-date").classed("local");
        var formatted = isLocal ? µ.toLocalISO(date) : µ.toUTCISO(date);
        d3.select("#data-date").text(formatted + " " + (isLocal ? "Local" : "UTC"));
        d3.select("#toggle-zone").text("⇄ " + (isLocal ? "UTC" : "Local"));
    }

    /**
     * Display the grids' types in the menu.
     */
    function showGridDetails(grids) {
        showDate(grids);
        var description = "";
        if (grids) {
            var langCode = d3.select("body").attr("data-lang") || "en";
            var primary = grids.primaryGrid, overlays = overlayGridsAgent.value();
            description = primary.description(langCode).name + primary.description(langCode).qualifier;
            if (overlays && overlays.grids === grids) {
                overlays.list.forEach(function(grid) {
                    if (grid !== primary) description += " + " + grid.description(langCode).name;
                });
            }
        }
        d3.select("#data-layer").text(description);
    }

    /**
     * Constructs a toggler for the specified product's units, storing the toggle state on the element having
     * the specified id. For example, given a product having units ["m/s", "mph"], the object returned by this
     * method sets the element's "data-index" attribute to 0 for m/s and 1 for mph. Calling value() returns the
     * currently active units object. Calling next() increments the index.
     */
    function createUnitToggle(id, product) {
        var units = product.units, size = units.length;
        var index = +(d3.select(id).attr("data-index") || 0) % size;
        return {
            value: function() {
                return units[index];
            },
            next: function() {
                d3.select(id).attr("data-index", index = ((index + 1) % size));
            }
        };
    }

    /**
     * Display the specified wind value. Allow toggling between the different types of wind units.
     */
    function showWindAtLocation(wind, product) {
        var unitToggle = createUnitToggle("#location-wind-units", product), units = unitToggle.value();
        d3.select("#location-wind").text(µ.formatVector(wind, units));
        d3.select("#location-wind-units").text(units.label).on("click", function() {
            unitToggle.next();
            showWindAtLocation(wind, product);
        });
    }

    /**
     * Display the overlays' values at [λ, φ], each with a button that toggles between its units.
     */
    function showOverlayValuesAtLocation(λ, φ, grids) {
        var element = d3.select("#location-value"), overlays = overlayGridsAgent.value();
        µ.removeChildren(element.node());
        if (!overlays || overlays.grids !== grids) return;
        overlays.list.forEach(function(grid) {
            var value = grid !== grids.primaryGrid ? grid.interpolate(λ, φ) : null;
            if (!µ.isValue(value)) return;
            var units = overlayUnits(grid);
            element.append("span").text(µ.formatScalar(value, units) + " ");
            element.append("span").attr("class", "text-button").text(units.label).on("click", function() {
                overlayUnitIndex[grid.type] = (overlayUnitIndex[grid.type] || 0) + 1;
                showOverlayValuesAtLocation(λ, φ, grids);
                showOverlayDetails(overlayGridsAgent.value());  // relabel its color scale in the new units
            });
            element.append("span").text("  ");
        });
    }

    // Stores the point and coordinate of the currently visible location. This is used to update the location
    // details when the field changes.
    var activeLocation = {};

    /**
     * Display a local data callout at the given [x, y] point and its corresponding [lon, lat] coordinates.
     * The location may not be valid, in which case no callout is displayed. Display location data for both
     * the primary grid and overlay grid, performing interpolation when necessary.
     */
    function showLocationDetails(point, coord) {
        point = point || [];
        coord = coord || [];
        var grids = gridAgent.value(), field = fieldAgent.value(), λ = coord[0], φ = coord[1];
        if (!field || !field.isInsideBoundary(point[0], point[1])) {
            return;
        }

        clearLocationDetails(false);  // clean the slate
        activeLocation = {point: point, coord: coord};  // remember where the current location is

        if (_.isFinite(λ) && _.isFinite(φ)) {
            d3.select("#location-coord").text(µ.formatCoordinates(λ, φ));
            d3.select("#location-close").classed("invisible", false);
        }

        if (field.isDefined(point[0], point[1]) && grids) {
            var wind = grids.primaryGrid.interpolate(λ, φ);
            if (µ.isValue(wind)) {
                showWindAtLocation(wind, grids.primaryGrid);
            }
            showOverlayValuesAtLocation(λ, φ, grids);
        }
    }

    function updateLocationDetails() {
        showLocationDetails(activeLocation.point, activeLocation.coord);
    }

    function clearLocationDetails(clearEverything) {
        d3.select("#location-coord").text("");
        d3.select("#location-close").classed("invisible", true);
        d3.select("#location-wind").text("");
        d3.select("#location-wind-units").text("");
        d3.select("#location-value").text("");
        d3.select("#location-value-units").text("");
        µ.removeChildren(d3.select("#location-underlay").node());
        d3.select("#location-area").text("");
        if (clearEverything) {
            activeLocation = {};
            d3.select(".location-mark").remove();
        }
    }

    function stopCurrentAnimation(alsoClearCanvas) {
        animatorAgent.cancel();
        if (alsoClearCanvas) {
            µ.clearCanvas(d3.select("#animation").node());
        }
    }

    /**
     * Registers a click event handler for the specified DOM element which modifies the configuration to have
     * the attributes represented by newAttr. An event listener is also registered for configuration change events,
     * so when a change occurs the button becomes highlighted (i.e., class ".highlighted" is assigned or removed) if
     * the configuration matches the attributes for this button. The set of attributes used for the matching is taken
     * from newAttr, unless a custom set of keys is provided.
     */
    function bindButtonToConfiguration(elementId, newAttr, keys) {
        keys = keys || _.keys(newAttr);
        d3.select(elementId).on("click", function() {
            if (d3.select(elementId).classed("disabled")) return;
            configuration.save(newAttr);
        });
        configuration.on("change", function(model) {
            var attr = model.attributes;
            d3.select(elementId).classed("highlighted", _.isEqual(_.pick(attr, keys), _.pick(newAttr, keys)));
        });
    }

    function reportSponsorClick(type) {
        if (ga) {
            ga("send", "event", "sponsor", type);
        }
    }

    /**
     * Registers all event handlers to bind components and page elements together. There must be a cleaner
     * way to accomplish this...
     */
    function init() {
        report.status("Initializing...");

        d3.select("#sponsor-link")
            .attr("target", µ.isEmbeddedInIFrame() ? "_new" : null)
            .on("click", reportSponsorClick.bind(null, "click"))
            .on("contextmenu", reportSponsorClick.bind(null, "right-click"))
        d3.select("#sponsor-hide").on("click", function() {
            d3.select("#sponsor").classed("invisible", true);
        });

        d3.selectAll(".fill-screen").attr("width", view.width).attr("height", view.height);
        fitDisplay();
        d3.select(window).on("resize", _.debounce(function() {
            var next = measureView();
            if (Math.abs(next.width - view.width) > RESIZE_TOLERANCE ||
                    Math.abs(next.height - view.height) > RESIZE_TOLERANCE) {
                // The window itself changed size (including a touch device turning between portrait and landscape):
                // resize the map's layers and build the globe again at the new size.
                view.width = next.width;
                view.height = next.height;
                d3.selectAll(".fill-screen").attr("width", view.width).attr("height", view.height);
                globeAgent.submit(buildGlobe, configuration.get("projection"));
                fitMenu();
            }
            fitDisplay();
        }, 150));
        fitMenu();
        if (document.fonts) {
            document.fonts.ready.then(fitMenu);
            document.fonts.addEventListener("loadingdone", fitMenu);
        }

        d3.select("#show-menu").on("click", function() {
            if (µ.isEmbeddedInIFrame()) {
                window.open("http://earth.nullschool.net/" + window.location.hash, "_blank");
            }
            else {
                d3.select("#menu").classed("invisible", !d3.select("#menu").classed("invisible"));
            }
        });

        d3.select("#show-layers").on("click", function() {
            d3.select("#layers").classed("invisible", !d3.select("#layers").classed("invisible"));
        });

        if (µ.isFF()) {
            // Workaround FF performance issue of slow click behavior on map having thick coastlines.
            d3.select("#display").classed("firefox", true);
        }

        // Tweak document to distinguish CSS styling between touch and non-touch environments. Hacky hack.
        if ("ontouchstart" in document.documentElement) {
            d3.select(document).on("touchstart", function() {});  // this hack enables :active pseudoclass
        }
        else {
            d3.select(document.documentElement).classed("no-touch", true);  // to filter styles problematic for touch
        }

        // Bind configuration to URL bar changes.
        d3.select(window).on("hashchange", function() {
            log.debug("hashchange");
            configuration.fetch({trigger: "hashchange"});
        });

        configuration.on("change", report.reset);

        meshAgent.listenTo(configuration, "change:topology", function(context, attr) {
            meshAgent.submit(buildMesh, attr);
        });

        globeAgent.listenTo(configuration, "change:projection", function(source, attr) {
            globeAgent.submit(buildGlobe, attr);
        });

        gridAgent.listenTo(configuration, "change", function() {
            var changed = _.keys(configuration.changedAttributes()), rebuildRequired = false;

            // Build a new grid if any layer-related attributes have changed. Overlays load on their own.
            if (_.intersection(changed, ["date", "hour", "param", "surface", "level", "region"]).length > 0) {
                rebuildRequired = true;
            }

            if (rebuildRequired) {
                gridAgent.submit(buildGrids);
            }
        });
        gridAgent.on("submit", function() {
            showGridDetails(null);
        });
        gridAgent.on("update", function(grids) {
            showGridDetails(grids);
            showSmokeDetails(grids);
        });
        configuration.on("change:flow", function() {
            showSmokeDetails(gridAgent.value());
        });
        d3.select("#toggle-zone").on("click", function() {
            d3.select("#data-date").classed("local", !d3.select("#data-date").classed("local"));
            showDate(gridAgent.cancel.requested ? null : gridAgent.value());
        });

        function startRendering() {
            rendererAgent.submit(buildRenderer, meshAgent.value(), globeAgent.value());
        }
        rendererAgent.listenTo(meshAgent, "update", startRendering);
        rendererAgent.listenTo(globeAgent, "update", startRendering);

        function startField() {
            fieldAgent.submit(createField, globeAgent.value(), gridAgent.value());
        }
        // True when the field belongs to the current globe and grids. It then follows moves by itself, so a move
        // needs no new field or animation, only an overlay redraw.
        function isCurrent() {
            var field = fieldAgent.value();
            return !!field && field.globe === globeAgent.value() && field.grids === gridAgent.value();
        }
        function unlessCurrent(f) {
            return function() {
                if (!isCurrent()) f.apply(this, arguments);
            };
        }

        fieldAgent.listenTo(gridAgent, "update", startField);
        fieldAgent.listenTo(rendererAgent, "render", unlessCurrent(startField));

        animatorAgent.listenTo(fieldAgent, "update", function(field) {
            animatorAgent.submit(animate, field);
        });
        animatorAgent.listenTo(rendererAgent, "start", unlessCurrent(stopCurrentAnimation.bind(null, true)));
        animatorAgent.listenTo(gridAgent, "submit", stopCurrentAnimation.bind(null, false));
        animatorAgent.listenTo(fieldAgent, "submit", stopCurrentAnimation.bind(null, false));

        // A GPU overlay is redrawn on every frame of a move. A CPU overlay is too slow for that, so it is cleared
        // when a move starts and rendered again when the move ends.
        function overlayFollowsMoves() {
            return isCurrent() && fieldAgent.value().gpu;
        }
        function redrawOverlay() {
            overlayAgent.submit(drawOverlay, fieldAgent.value(), overlayGridsAgent.value());
        }
        function startOverlayGrids() {
            overlayGridsAgent.submit(buildOverlayGrids, gridAgent.value());
        }
        overlayGridsAgent.listenTo(gridAgent, "update", startOverlayGrids);
        overlayGridsAgent.listenTo(configuration, "change:overlayType", startOverlayGrids);
        overlayGridsAgent.on("update", function(overlays) {
            showOverlayDetails(overlays);
            showGridDetails(gridAgent.value());
            updateLocationDetails();
        });
        overlayAgent.listenTo(overlayGridsAgent, "update", redrawOverlay);
        overlayAgent.listenTo(fieldAgent, "update", redrawOverlay);
        overlayAgent.listenTo(rendererAgent, "start", function() {
            if (!overlayFollowsMoves()) {
                overlayAgent.submit(drawOverlay, fieldAgent.value(), overlayGridsAgent.value(), true);
            }
        });
        overlayAgent.listenTo(rendererAgent, "redraw", function() {
            if (overlayFollowsMoves()) {
                drawOverlay(fieldAgent.value(), overlayGridsAgent.value());
            }
        });
        overlayAgent.listenTo(rendererAgent, "render", function() {
            if (isCurrent() && !fieldAgent.value().gpu) {
                redrawOverlay();
            }
        });
        overlayAgent.listenTo(configuration, "change:showGridPoints", redrawOverlay);

        // Hotspots are redrawn on every frame of a move in every projection, as plain squares until the move ends.
        fireAgent.listenTo(gridAgent, "update", function(grids) {
            fireAgent.submit(loadFires, grids);
        });
        fireAgent.on("update", function(fires) {
            showFireDetails(fires);
            drawFires();
        });
        fireAgent.listenTo(rendererAgent, "update redraw render", function() { drawFires(); });
        areaAgent.listenTo(gridAgent, "update", function(grids) {
            areaAgent.submit(loadFireAreas, grids);
        });
        areaAgent.on("update", function(areas) {
            showAreaDetails(areas);
            showHeatFrameDetails();
            drawFires();
        });
        transitionAgent.listenTo(configuration, "change:transition change:areas change:date change:hour", function() {
            transitionAgent.submit(loadTransition, _.clone(configuration.attributes));
        });
        transitionAgent.on("update", function() {
            showAreaDetails(areaAgent.value());
            drawFires();
        });
        framesAgent.listenTo(gridAgent, "update", function(grids) {
            stopHeatFrames();
            framesAgent.submit(loadHeatFrames, grids);
        });
        framesAgent.on("update", function() {
            showHeatFrameDetails();
            drawFires();
        });

        // The underlay is redrawn on every frame of a move, and again as its tiles arrive.
        rendererAgent.on("update redraw render", drawUnderlay);
        configuration.on("change:underlay change:map change:region", function() {
            if (configuration.get("underlay") === "map") {  // older links: the map is now a toggle
                return configuration.save({underlay: "none", map: "on"});
            }
            drawUnderlay();
            showUnderlayDetails();
        });
        globeAgent.on("update", showUnderlayDetails);
        fireAgent.on("update", function() {  // the thermal underlay shows the hotspots' day
            drawUnderlay();
            if (UNDERLAYS[configuration.get("underlay")] && UNDERLAYS[configuration.get("underlay")].thermal) {
                showUnderlayDetails();
            }
        });

        // Add event handlers for showing, updating, and removing location details.
        inputController.on("click", showLocationDetails);
        inputController.on("click", showUnderlayAtLocation);
        inputController.on("click", showAreaAtLocation);
        fieldAgent.on("update", updateLocationDetails);
        d3.select("#location-close").on("click", _.partial(clearLocationDetails, true));

        // Modify menu depending on what mode we're in.
        configuration.on("change:param", function(context, mode) {
            d3.selectAll(".ocean-mode").classed("invisible", mode !== "ocean");
            d3.selectAll(".wind-mode").classed("invisible", mode !== "wind");
            switch (mode) {
                case "wind":
                    d3.select("#nav-backward-more").attr("title", "-1 Day");
                    d3.select("#nav-backward").attr("title", "-3 Hours");
                    d3.select("#nav-forward").attr("title", "+3 Hours");
                    d3.select("#nav-forward-more").attr("title", "+1 Day");
                    break;
                case "ocean":
                    d3.select("#nav-backward-more").attr("title", "-1 Month");
                    d3.select("#nav-backward").attr("title", "-5 Days");
                    d3.select("#nav-forward").attr("title", "+5 Days");
                    d3.select("#nav-forward-more").attr("title", "+1 Month");
                    break;
            }
        });

        // Add handlers for mode buttons.
        d3.select("#wind-mode-enable").on("click", function() {
            if (configuration.get("param") !== "wind") {
                configuration.save({param: "wind", surface: "surface", level: "level", overlayType: "default"});
            }
        });
        configuration.on("change:param", function(x, param) {
            d3.select("#wind-mode-enable").classed("highlighted", param === "wind");
        });
        d3.select("#ocean-mode-enable").on("click", function() {
            if (configuration.get("param") !== "ocean") {
                // When switching between modes, there may be no associated data for the current date. So we need
                // find the closest available according to the catalog. This is not necessary if date is "current".
                // UNDONE: this code is annoying. should be easier to get date for closest ocean product.
                var ocean = {param: "ocean", surface: "surface", level: "currents", overlayType: "default"};
                var attr = _.clone(configuration.attributes);
                if (attr.date === "current") {
                    configuration.save(ocean);
                }
                else {
                    when.all(products.productsFor(_.extend(attr, ocean))).spread(function(product) {
                        if (product.date) {
                            configuration.save(_.extend(ocean, µ.dateToConfig(product.date)));
                        }
                    }).otherwise(report.error);
                }
                stopCurrentAnimation(true);  // cleanup particle artifacts over continents
            }
        });
        configuration.on("change:param", function(x, param) {
            d3.select("#ocean-mode-enable").classed("highlighted", param === "ocean");
        });

        // Add logic to disable buttons that are incompatible with each other.
        configuration.on("change:overlayType", function() {
            var types = overlayTypes();
            d3.select("#surface-level").classed("disabled",
                _.contains(types, "air_density") || _.contains(types, "wind_power_density"));
        });
        configuration.on("change:surface", function(x, s) {
            d3.select("#overlay-air_density").classed("disabled", s === "surface");
            d3.select("#overlay-wind_power_density").classed("disabled", s === "surface");
        });

        // Add event handlers for the time navigation buttons.
        d3.select("#nav-backward-more").on("click", navigate.bind(null, -10));
        d3.select("#nav-forward-more" ).on("click", navigate.bind(null, +10));
        d3.select("#nav-backward"     ).on("click", navigate.bind(null, -1));
        d3.select("#nav-forward"      ).on("click", navigate.bind(null, +1));
        d3.select("#nav-now").on("click", function() { configuration.save({date: "current", hour: ""}); });

        d3.select("#option-show-grid").on("click", function() {
            configuration.save({showGridPoints: !configuration.get("showGridPoints")});
        });
        configuration.on("change:showGridPoints", function(x, showGridPoints) {
            d3.select("#option-show-grid").classed("highlighted", showGridPoints);
        });

        // Add handlers for all wind level buttons.
        d3.selectAll(".surface").each(function() {
            var id = this.id, parts = id.split("-");
            bindButtonToConfiguration("#" + id, {param: "wind", surface: parts[0], level: parts[1]});
        });

        // Add handlers for ocean animation types.
        bindButtonToConfiguration("#animate-currents", {param: "ocean", surface: "surface", level: "currents"});

        // Air overlay buttons switch their overlay on and off, so several can be shown at once.
        AIR_OVERLAYS.forEach(function(type) {
            var id = "#overlay-" + type;
            d3.select(id).on("click", function() {
                if (d3.select(id).classed("disabled")) return;
                var on = configuration.get("param") === "wind" ? overlayTypes() : [];
                on = _.contains(on, type) ? _.without(on, type) : on.concat(type);
                on = AIR_OVERLAYS.filter(function(t) { return _.contains(on, t); });  // keep the draw order
                var value = on.length === 0 ? "off" : on.length === 1 && on[0] === "wind" ? "default" : on.join(",");
                configuration.save({param: "wind", overlayType: value});
            });
            configuration.on("change", function() {
                var on = configuration.get("param") === "wind" && _.contains(overlayTypes(), type);
                d3.select(id).classed("highlighted", on);
            });
        });
        bindButtonToConfiguration("#overlay-off", {overlayType: "off"});
        d3.select("#overlay-hotspots").on("click", function() {
            configuration.save({hotspots: configuration.get("hotspots") === "off" ? "on" : "off"});
        });
        d3.select("#overlay-areas").on("click", function() {
            configuration.save({areas: configuration.get("areas") === "off" ? "on" : "off"});
        });
        d3.select("#overlay-frames").on("click", function() {
            // Heat frames replace the whole day's hotspots, which would cover them.
            var on = configuration.get("frames") !== "on";
            configuration.save(on ? {frames: "on", hotspots: "off"} : {frames: "off"});
        });
        configuration.on("change:frames", function(x, frames) {
            d3.select("#overlay-frames").classed("highlighted", frames === "on");
            if (frames !== "on") stopHeatFrames();
            showHeatFrameDetails();
            drawFires();
        });
        configuration.on("change:areas", function(x, areas) {
            d3.select("#overlay-areas").classed("highlighted", areas !== "off");
            showAreaDetails(areaAgent.value());
            drawFires();
        });
        configuration.on("change:hotspots", function(x, hotspots) {
            d3.select("#overlay-hotspots").classed("highlighted", hotspots !== "off");
            showFireDetails(fireAgent.value());
            drawFires();
        });
        bindButtonToConfiguration("#overlay-ocean-off", {overlayType: "off"});
        bindButtonToConfiguration("#overlay-currents", {overlayType: "default"});
        _.keys(PARTICLE_DENSITY).forEach(function(name) {
            bindButtonToConfiguration("#particles-" + name, {particles: name});
        });
        ["wind", "smoke"].forEach(function(name) {
            bindButtonToConfiguration("#flow-" + name, {flow: name});
        });
        d3.select("#flow-smoke").on("click", function() {  // smoke is drawn with particles, which start off
            configuration.save(configuration.get("particles") === "off" ? {flow: "smoke", particles: "some"} : {flow: "smoke"});
        });
        _.without(_.keys(UNDERLAYS), "map").concat("none").forEach(function(name) {
            bindButtonToConfiguration("#underlay-" + name, {underlay: name});
        });
        // The Map toggle goes with any underlay.
        d3.select("#underlay-map").on("click", function() {
            configuration.save({map: configuration.get("map") === "on" ? "off" : "on"});
        });
        configuration.on("change:map", function(x, map) {
            d3.select("#underlay-map").classed("highlighted", map === "on");
        });

        // Regions without data yet stay in the menu, disabled; an unknown or unavailable one in the URL becomes the
        // default.
        _.each(products.regions, function(region, key) {
            d3.select("#region-" + key).classed("disabled", !region.bounds)
                .attr("title", region.bounds ? "Load " + region.name : region.name + ": not available yet");
            bindButtonToConfiguration("#region-" + key, {region: key, orientation: ""}, ["region"]);
        });
        configuration.on("change:region", function(x, region) {
            if (products.region(region).key !== region) configuration.save({region: products.region(region).key});
        });

        // Add handlers for all projection buttons.
        globes.keys().forEach(function(p) {
            bindButtonToConfiguration("#" + p, {projection: p, orientation: ""}, ["projection"]);
        });
    }

    function start() {
        // Everything is now set up, so load configuration from the hash fragment and kick off change events.
        configuration.fetch();
    }

    when(true).then(init).then(start).otherwise(report.error);

})();
