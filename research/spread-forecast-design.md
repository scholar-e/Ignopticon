Then I would design the project primarily as a **near-real-time wildfire spread forecasting system**, with fire-risk mapping as a secondary feature.

The central research question would be:

**Can a physics-based wildfire simulator, continuously corrected using VIIRS satellite observations, predict the future movement of an active fire more accurately than the same simulator without satellite correction?**

That gives you a concrete, testable improvement over existing models, rather than just another wildfire visualization application.

## 1. Recommended model architecture

<box border radius="lg" padding={3} gap={2}>
  <box background="surface-secondary" radius="md" padding={3} gap={1} align="center">
    **Satellite observations**
    <text color="secondary" size="xs" textAlign="center">VIIRS hotspots, fire radiative power, timestamps</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <grid columns={2} gap={2}>
    <grid-item>
      <box border radius="md" padding={3} align="center" gap={1}>
        **Weather**
        <text color="secondary" size="xs" textAlign="center">Wind, humidity, precipitation</text>
      </box>
    </grid-item>
    <grid-item>
      <box border radius="md" padding={3} align="center" gap={1}>
        **Landscape**
        <text color="secondary" size="xs" textAlign="center">Fuel, canopy, elevation, slope</text>
      </box>
    </grid-item>
  </grid>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box background="surface-secondary" radius="md" padding={3} gap={1} align="center">
    **ELMFIRE simulation ensemble**
    <text color="secondary" size="xs" textAlign="center">Multiple weather, fuel and ignition-state scenarios</text>
  </box>
  <row justify="center">
    <icon name="arrow-down" color="secondary"/>
  </row>
  <box border radius="md" padding={3} align="center" gap={1}>
    **Predictions**
    <text color="secondary" size="xs" textAlign="center">1h, 3h, 6h, 12h and 24h spread forecasts</text>
  </box>
  <row justify="center">
    <icon name="refresh-cw" color="secondary"/>
  </row>
  <text color="secondary" size="xs" textAlign="center">Reinitialize or update the model when new observations arrive</text>
</box>

I would treat ELMFIRE as the primary engine and use WRF-SFIRE for additional research on events where fire-generated winds and complex atmospheric dynamics appear particularly important.

## 2. What the user-facing application should show

<box gap={3}>
  <row gap={3} align="start">
    <AsyncImage query="wildfire predicted perimeter overlay map satellite active fire spots colored time series fire spread forecasting GIS" aspectRatio="4:3" maxWidth="148px"/>
    <box flex="1" gap={1}>
      **Active wildfire forecast**

      Show observed fire detections and estimated active perimeter separately from predicted perimeters at future timestamps. Include fire arrival times and uncertainty.
    </box>
  </row>
  <divider color="subtle"/>
  <row gap={3} align="start">
    <AsyncImage query="wildfire burn probability map heatmap red yellow green forecast model GIS" aspectRatio="4:3" maxWidth="148px"/>
    <box flex="1" gap={1}>
      **Burn-probability zones**

      Show the estimated probability that each region will burn within a chosen forecast horizon. This is more useful than displaying just one forecast line.
    </box>
  </row>
  <divider color="subtle"/>
  <row gap={3} align="start">
    <AsyncImage query="wildfire risk map United States forest fire danger vegetation weather heatmap GIS" aspectRatio="4:3" maxWidth="148px"/>
    <box flex="1" gap={1}>
      **General fire-risk layer — optional**

      A separate map based on fuel dryness, vegetation, topography, and fire weather. Clearly distinguish ignition risk from the likelihood of being reached by an existing fire.
    </box>
  </row>
</box>

The first two should receive almost all development effort. A useful, validated spread forecast is more scientifically substantial than adding a broad ignition-risk map without reliable training data.

## 3. The first major technical challenge: reconstructing the active fire perimeter

VIIRS detections should not simply be connected into a polygon. A hotspot can represent only part of a satellite pixel, and gaps between detections do not necessarily imply unburned fuel.

I would initially use a two-stage procedure:

1. **Construct an estimated fire state.** Combine timestamped VIIRS detections with the most recent independently mapped perimeter, if available. Assign uncertainty to areas without direct observations.
2. **Initialize ELMFIRE from that state.** ELMFIRE supports an initial active-fire raster through its `PHI_FILENAME` parameter, where negative values identify burned cells and the zero level set represents the fire front. <Cite ref="turn657686search6"/>

This matters because the model can then start from an established fire, instead of attempting to reproduce its entire history from an ignition point.

For updates, I would test an ensemble-based assimilation method: keep multiple plausible fire states, compare their predicted thermal detections against incoming VIIRS data, and adjust the states or their weights accordingly.

## 4. The biggest practical limitation is observation frequency

NASA FIRMS can deliver VIIRS observations quickly—sometimes within minutes of satellite acquisition in the U.S. and Canada—but **rapid delivery does not mean continuous satellite coverage**. Polar-orbiting satellites only observe a given location during their overpasses. <Cite refs={["turn657686search0","turn657686search3"]}/>

Your simulator should therefore predict between observations, using weather forecasts and the last known fire state.

A particularly useful extension would be to integrate GOES geostationary fire detections where available. Their more frequent observations could help constrain changes between VIIRS overpasses, although their spatial resolution is coarser.

## 5. A realistic development sequence

| Phase | Deliverable | What it demonstrates |
|---|---|---|
| 1 | Historical-fire replay | ELMFIRE can reproduce fire growth from supplied conditions |
| 2 | VIIRS-based initialization | The model can estimate the state of an active fire |
| 3 | Forecast updates | Incoming observations improve subsequent predictions |
| 4 | Calibrated ensembles | The system predicts burn probabilities, not just a single perimeter |
| 5 | ML spread correction | Historical data can improve the underlying model |
| 6 | Risk/suppression scenarios | Additional decision-support functionality |

**I would make Phase 3 the central technical achievement of your project.** If you can demonstrate that forecasts initialized or updated with VIIRS outperform forecasts without those updates on held-out historical incidents, you have a meaningful research result.

For the Space Apps submission, retain the MODIS–VIIRS harmonized burning calendar as a separate, clearly accessible feature. The official challenge specifically requests that historical harmonization and calendar functionality. <Cite ref="turn657686search5"/>

## 6. One experiment I'd prioritize

Take 10–20 historical wildfires with sufficiently detailed time-sequenced perimeter observations and divide them by whole incidents into development and held-out evaluation sets.

For each held-out fire, evaluate three configurations:

| Configuration | Input | Purpose |
|---|---|---|
| A: Baseline | Initial perimeter, fuels, weather | Conventional simulator |
| B: Satellite-updated | Same inputs plus VIIRS assimilation | Measure value of satellite correction |
| C: Satellite + ML | B plus learned spread correction | Measure additional value of training |

Evaluate each at 3, 6, 12, and 24 hours using spatial overlap, perimeter displacement, arrival-time error, and calibrated burn probabilities. Use only observations that would have existed before each forecast issuance time.

The key outcome would be whether B consistently outperforms A, and whether C improves on B without overfitting to particular fires.

**My recommendation is to defer detailed firefighting simulation until this experiment works.** A moisture-based suppression layer is feasible, but the ability to forecast untreated fire spread accurately is a prerequisite for making credible claims about the benefits of intervention.

The resulting project would have two complementary outputs: a Space Apps-compliant MODIS/VIIRS historical activity application and a technically differentiated active-fire forecasting system built around ELMFIRE and satellite assimilation.

---

If you want, I can:

- Explore integrating VIIRS data for higher-resolution fire detection
- Plan a workflow using ELMFIRE and VIIRS assimilation
- Identify key data needs specific to VIIRS-based fire modeling