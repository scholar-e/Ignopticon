# Building an Advanced Wildfire Prediction Model with Satellite Data and Supercomputing

Given access to MODIS infrared observations, geographic information, vegetation-cover data, and substantial supercomputing resources, I would prioritize **ELMFIRE and WRF-SFIRE**, with QUIC-Fire as a potential third component for high-resolution research.

However, I would not simply increase the spatial resolution of an existing simulator. The larger opportunity is building a **physics-informed, observation-assimilating wildfire prediction system** that continuously adjusts its predictions against satellite observations.

The fundamental challenge is that computing power is only one bottleneck. The others are incomplete knowledge of fuel conditions, weather uncertainty, and the fact that satellite infrared measurements do not directly reveal the entire burning perimeter.

## 1. Which models are worth extending?

<box gap={3}>
  <row align="start" gap={3}>
    <box flex="1" gap={1}>
      <row align="center" gap={2}>
        <title size="lg">1. ELMFIRE</title>
        <badge color="success">Best starting point</badge>
      </row>
      Open-source, parallelized fire-spread model with geospatial inputs and suppression capabilities.

      **What I'd develop:** satellite data assimilation, learned spread corrections, fuel moisture estimation, and uncertainty-calibrated ensemble prediction.
    </box>
  </row>
  <divider color="subtle"/>
  <box gap={1}>
    <title size="lg">2. WRF-SFIRE</title>
    <text color="secondary" size="xs">Best for exploiting extensive HPC resources</text>
    Coupled atmospheric and fire model, allowing the fire to influence wind fields and the changing winds to affect fire spread.

    **What I'd develop:** high-resolution weather coupling, fire-state assimilation, and a multiscale forecasting pipeline using observational data.
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <title size="lg">3. QUIC-Fire</title>
    <text color="secondary" size="xs">Best for fine-scale physical research</text>
    Uses three-dimensional wind and vegetation characteristics, making it interesting for fuel structure, local dynamics, and simplified firefighting interventions.

    **What I'd develop:** fine-scale numerical experiments and models for how vegetation, moisture, and local wind affect fire behavior. Engine access is a constraint.
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <title size="lg">4. Cell2Fire</title>
    <text color="secondary" size="xs">Best for experimenting with new algorithms at scale</text>
    A relatively modular framework for simulating fire spread across landscape cells.

    **What I'd develop:** learned propagation rules, probabilistic cell transitions, and large ensembles of weather and suppression scenarios.
  </box>
</box>

ELMFIRE already accepts fuel, terrain, canopy, moisture, and time-varying weather data as gridded inputs, making it a practical foundation. WRF-SFIRE is especially relevant because NOAA's NightFOX research explicitly explores assimilating new airborne observations into coupled fire–atmosphere forecasts. <Cite refs={["turn209582search2","turn209582search4"]}/>

The important architectural distinction is that **ELMFIRE is likely a better fast forecasting engine, while WRF-SFIRE is a better high-complexity physics engine**. These can complement rather than replace one another.

## 2. Do tree topology and canopy datasets exist?

Yes. There is substantially more public vegetation information than just the percentage of land covered by trees.

The relevant term is usually *forest structure*, rather than topology.

<box gap={2}>
  <row align="start" gap={3}>
    <AsyncImage query="USGS 3DEP airborne lidar forest canopy point cloud colored by height forest trees" aspectRatio="4:3" maxWidth="145px"/>
    <box flex="1" gap={1}>
      **LiDAR point clouds**

      Can reveal the three-dimensional structure of a forest, including individual crowns, canopy height, and sometimes understory structure. High-density airborne LiDAR is particularly valuable for estimating vertical fuel continuity.
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <AsyncImage query="NASA GEDI forest canopy vertical profile lidar footprint visualization" aspectRatio="4:3" maxWidth="145px"/>
    <box flex="1" gap={1}>
      **NASA GEDI**

      Spaceborne LiDAR measurements that provide sampled forest height and vertical structure. GEDI does not produce a dense, complete map of every tree, but its measurements can help estimate forest structural properties at scale.
    </box>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <AsyncImage query="LANDFIRE canopy height forest canopy cover raster map wildfire fuels" aspectRatio="4:3" maxWidth="145px"/>
    <box flex="1" gap={1}>
      **LANDFIRE**

      Already provides many fire-model-ready layers across the United States, including canopy height, cover, base height, bulk density, and surface fuel classifications.
    </box>
  </row>
</box>

A 2026 study demonstrated the use of GEDI and airborne LiDAR to estimate canopy base height and canopy bulk density—two particularly relevant variables for modeling crown-fire behavior. <Cite ref="turn209582search15"/>

### What matters about individual trees?

| Variable | Relevance to fire simulation | Availability |
|---|---|---|
| Canopy cover | Shading, wind reduction, vegetation continuity | Widely available |
| Tree height | Crown structure and wind exposure | Widely available as estimates |
| Canopy base height | Potential transition from surface to crown fire | LANDFIRE, LiDAR-derived |
| Canopy bulk density | Ability of crown fire to propagate | LANDFIRE, LiDAR-derived |
| Tree species | Fuel characteristics and combustibility | Regional inventories and classified imagery |
| Understory vegetation | Surface fuel and vertical fire propagation | More difficult to observe |
| Dead fuel loading | How much combustible material is present | Modeled and measured estimates |
| Individual tree locations | Localized fire behavior and structure | Possible with high-density LiDAR |

For most landscape-scale forecasts, **accurate fuel type, canopy structure, and fuel moisture matter more than explicitly identifying every individual tree**.

I would not reconstruct every tree across a large wildfire domain unless fine-scale experiments demonstrate a measurable forecast improvement.

## 3. Your MODIS data is valuable, but its resolution is a major limitation

MODIS active-fire detections are approximately 1 km in nominal spatial resolution. This is relatively coarse compared with the scale at which fires actually advance.

For comparison:

| Source | Nominal spatial resolution | Principal contribution |
|---|---:|---|
| MODIS active fire | 1,000 m | Historical detections, active fire locations, radiative power |
| VIIRS active fire | 375 m | Smaller-fire detection and better active-front localization |
| Landsat imagery | 30 m for common multispectral bands | Burn severity, vegetation, historical burn mapping |
| Airborne infrared | Varies by sensor and mission | More detailed active-front and perimeter observations |
| Airborne LiDAR | Often meter-scale or finer | Forest structure and fuel characterization |

The 1 km MODIS detection footprint is not a precise 1 km square of burning vegetation. A detection indicates that the sensor observed a thermal anomaly within its footprint. A fire can occupy only a portion of that footprint.

A peer-reviewed paper published on October 26, 2026, quantified just how important observation uncertainty is. It estimated an MODIS fire radiative power detection limit of approximately 3.7 MW under favorable nighttime conditions, rising as high as 40 MW for some daytime off-nadir observations. These limits make absence of detection unreliable as proof that an area is not burning. <Cite ref="turn209582search5"/>

I would therefore use MODIS for *observational constraints*, not as exact ground-truth fire boundaries.

### A better way to incorporate MODIS

Suppose a simulator predicts the state of a fire at a given time:

\[
S_t=\{\text{burned area, active fronts, fuel state, intensity}\}
\]

Instead of comparing the model's 30 m or 100 m grid directly against MODIS's 1 km detections, create a satellite observation operator:

\[
\hat y_t=H(S_t,\theta_{\mathrm{sensor}})
\]

Here, \(H\) approximates what MODIS would have detected, given the simulated fire and the observation geometry, detection threshold, cloud conditions, and other sensor characteristics.

You can then compare the simulated satellite observation \(\hat y_t\) with the real observation \(y_t\).

That approach avoids penalizing a physically plausible simulation simply because the satellite failed to detect a small or obscured fire. The 2026 paper specifically develops observation operators for MODIS and VIIRS products. <Cite ref="turn209582search3"/>

## 4. What other information do you need to train the model?

This is the most important part of the project.

A traditional wildfire simulator is not necessarily trained like a neural network. It is initialized using environmental data, calibrated using observations, and then run forward.

You could also add trainable components that learn corrections to its underlying physical equations.

### Training and input data requirements

| Information | Needed for | Priority |
|---|---|---|
| Historical timestamped fire perimeters | Training spread corrections and evaluating predictions | Essential |
| Wind speed and direction over time | Fire propagation and changing direction | Essential |
| Fuel moisture | Determining fuel ignition and spread potential | Essential |
| Fuel model and vegetation type | Surface fire behavior | Essential |
| Elevation, slope, aspect | Terrain effects | Essential |
| Canopy height, cover, density | Crown fire and wind interaction | High |
| Fire radiative power and hotspots | Fire-state assimilation, intensity constraints | High |
| Pre-fire vegetation condition | Reconstructing historical fuel state | High |
| Soil moisture, drought and precipitation | Fuel moisture estimation | High |
| Spot-fire observations | Modeling firebrand-caused ignitions | Medium–high |
| Suppression activity records | Intervention modeling | Essential only for suppression learning |

**The distinction between historical and current data is critical.** If you train on a fire from 2018, you should reconstruct the vegetation and moisture conditions that existed in 2018, not use a 2026 fuel map indiscriminately.

Likewise, retrospective training can use historical meteorological analyses, but prospective validation should use weather forecasts that were actually available at the time of each prediction.

### Weather data is especially important

You would ideally need:

- Near-surface wind fields, preferably at fine temporal and spatial intervals.
- Wind gusts and changes in direction.
- Temperature and relative humidity.
- Precipitation and accumulated drying conditions.
- Vertical atmospheric profiles if using WRF-SFIRE.
- Estimates of dead and live fuel moisture.

So although forest structure matters, I would invest in the wind and fuel-moisture data pipeline before undertaking full individual-tree reconstruction.

### What would the machine learning component actually learn?

I see three plausible approaches.

<box gap={3}>
  <box gap={1}>
    **Approach A — Learn corrections to the spread-rate equation**

    Keep the conventional fire model, but train an ML system to estimate a correction factor based on observed environmental conditions.

    \[
    R_{\mathrm{corrected}}=R_{\mathrm{physical}}\cdot e^{f_\theta(X)}
    \]

    Here \(R_{\mathrm{physical}}\) is the baseline spread rate, and \(f_\theta(X)\) learns where that model is systematically inaccurate.

    This is my preferred initial ML approach because it preserves a physically interpretable simulation while permitting data-driven improvements.
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    **Approach B — Train a neural surrogate of a detailed simulator**

    Run WRF-SFIRE or another sophisticated simulation repeatedly, generating a large dataset of fire states and future evolution.

    Train a neural operator or other surrogate model to predict the simulator's next state much faster.

    This could be useful for generating thousands of real-time forecast scenarios, but the surrogate initially learns the simulator's behavior and biases—not necessarily the behavior of real fires.
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    **Approach C — Train an observation-assimilation system**

    Learn how to adjust the estimated fire state when new satellite observations arrive.

    This could be combined with an Ensemble Kalman Filter, particle filter, or variational assimilation process, depending on the complexity of the latent fire state.

    Importantly, a learned system could improve uncertainty estimates and satellite observation interpretation without replacing the underlying fire physics.
  </box>
</box>

There is already a strong research precedent for approach C. A 2025 study of the 2021 Caldor Fire compared different ways of incorporating airborne IR perimeters and satellite detections into a coupled fire–atmosphere model. The method using observations from consecutive satellite acquisitions to distinguish actively growing from inactive fronts performed best in that study, improving forecasts particularly at 24–48-hour horizons. <Cite ref="turn148750search5"/>

---

## 5. How I would use your supercomputing resources

Supercomputing creates three meaningful opportunities.

**First, run large ensembles.** Instead of giving one deterministic prediction, perturb wind forecasts, fuel moisture, fire-state estimates, and model parameters across many simulations. The outcome is a probability distribution of potential future fire locations.

**Second, run coupled atmospheric simulations.** WRF-SFIRE can represent fire-induced changes to local winds and atmospheric conditions, which ordinary one-way-coupled spread models cannot fully reproduce. This may matter during strong convective activity, though the model still needs validation against observed fires.

**Third, build a training dataset from high-resolution simulations.** Use expensive physics-informed simulations to explore environmental conditions inadequately represented in observations. These synthetic data can help pretrain surrogate models, while real fire observations are used for calibration and validation.

My preferred system would therefore have two computational tiers:

<box border radius="lg" padding={3} gap={2}>
  <row justify="center" align="center">
    <box background="surface-secondary" radius="md" padding={3} flex="1" gap={1}>
      <text weight="medium" textAlign="center">High-resolution research tier</text>
      <text size="xs" color="secondary" textAlign="center">WRF-SFIRE, detailed fuel/atmospheric scenarios</text>
    </box>
  </row>
  <row justify="center">
    <icon name="arrow-down-up" color="secondary" size="lg"/>
  </row>
  <row justify="center" align="center">
    <box background="surface-secondary" radius="md" padding={3} flex="1" gap={1}>
      <text weight="medium" textAlign="center">Fast operational tier</text>
      <text size="xs" color="secondary" textAlign="center">ELMFIRE + learned corrections + satellite assimilation</text>
    </box>
  </row>
  <row justify="center">
    <icon name="arrow-down" color="secondary" size="lg"/>
  </row>
  <row justify="center">
    <box border radius="md" padding={3} flex="1" gap={1}>
      <text weight="medium" textAlign="center">Probabilistic forecast</text>
      <text size="xs" color="secondary" textAlign="center">Time-of-arrival maps, intensity, burn probability, uncertainty</text>
    </box>
  </row>
</box>

The fast tier can rerun when observations arrive, while the slower tier contributes forecasts and training examples for more difficult fire–atmosphere conditions.

This is a proposed architecture, not an existing, benchmarked combination of the two engines.

## 6. Your firefighting simplification: treating suppression as a cooled region

This is feasible as a first approximation, but I would make one important change.

**Model the effects on fuel temperature, fuel moisture, and combustibility separately, rather than using only a temperature reduction.**

The reason is that different suppression methods work through different mechanisms.

For instance, plain water absorbs heat and raises fuel moisture. Long-term retardant changes the combustion characteristics of treated fuels, even after its water evaporates. The U.S. Forest Service explicitly distinguishes these mechanisms. <Cite ref="turn148750search48"/>

### A minimal suppression model

Consider representing each cell of the landscape with additional state variables:

\[
S_i(t)=\{T_i,M_i,F_i,C_i\}
\]

Where:

- \(T_i\): effective temperature or thermal state.
- \(M_i\): fuel moisture content.
- \(F_i\): remaining available fuel.
- \(C_i\): suppression-treatment effectiveness.

For an illustrative water application at time \(t_0\), you could introduce:

\[
M_i(t_0^+)=M_i(t_0^-)+\Delta M_i
\]

Then evolve the additional moisture approximately as:

\[
\Delta M_i(t)=\Delta M_i(t_0)e^{-k(t-t_0)}
\]

The constant \(k\) would represent drying and loss of the moisture effect. In a more accurate model, \(k\) would depend on wind, temperature, humidity, fuel type, and radiation.

The local rate of spread could then be changed through the model's existing moisture-sensitive spread equations, rather than applying an arbitrary reduction.

For example:

\[
R_i(t)=R_{\mathrm{base},i}(t)\cdot g(M_i(t),C_i(t))
\]

where \(g\) is a calibrated modifier representing moisture and other suppression effects.

This is a conceptual model, not a validated equation for water-drop effectiveness.

### Why cooling alone is not sufficient

A region that has been cooled can reignite as it dries. A water drop also does not necessarily suppress all portions of the vegetation equally: water penetration, fuel geometry, canopy interception, and application intensity matter.

Firebrands may ignite vegetation beyond the treated region. Furthermore, a fire front may be slowed rather than completely stopped.

Therefore, I would give the model different intervention types:

| Intervention | Simplified representation |
|---|---|
| Water drop | Temporary moisture increase and heat removal |
| Foam or water enhancer | Modified water retention, penetration, and drying |
| Long-term retardant | Persistent modification of effective combustibility |
| Firebreak | Local fuel removal or reduction |
| Fireline construction | A barrier with a probability of holding |
| Prescribed or backburning | Deliberate ignition and subsequent fuel consumption |

You would not need to simulate individual aircraft or firefighters initially. An interface could specify that a polygon received a given treatment at a particular time, and the simulation could apply a parameterized effect.

### A critical constraint: MODIS cannot validate individual suppression events well

With 1 km MODIS fire detections, it would be difficult to determine whether a comparatively narrow retardant line or water drop actually prevented fire spread.

You would need higher-resolution before-and-after observations and preferably records of where and when the suppression was applied.

Otherwise, you risk attributing a change in fire growth to firefighting when it was actually caused by fuel discontinuity, terrain, or changing wind.

ELMFIRE also provides an alternative baseline: its experimental suppression module already includes spatially explicit direct and indirect attack, fireline capacity, and probabilistic containment. <Cite ref="turn148750search3"/>

I would compare your moisture-based suppression model against that existing module rather than immediately replacing it.

## 7. What I would build first

The most efficient research sequence would be:

<list marker="number" connector="solid" gap={2}>
  <list-item>
    **Establish a baseline with ELMFIRE.** Reproduce historical wildfires using LANDFIRE, elevation data, reconstructed weather, and timestamped perimeters. Measure forecast errors at several horizons.
  </list-item>
  <list-item>
    **Add MODIS and VIIRS assimilation.** Implement observation operators that account for detection uncertainty. Test whether sequential satellite updates improve predictions over perimeter-only initialization.
  </list-item>
  <list-item>
    **Improve vegetation and moisture estimation.** Test LANDFIRE against augmented LiDAR/GEDI-derived features. Determine whether additional structural information measurably improves forecasts.
  </list-item>
  <list-item>
    **Train a spread-rate correction model.** Start with a relatively constrained learned adjustment, validating on entire wildfires that were excluded from training.
  </list-item>
  <list-item>
    **Use WRF-SFIRE selectively.** Investigate whether two-way atmospheric coupling improves forecasts on difficult historical incidents, and whether a surrogate can capture useful corrections at lower computational cost.
  </list-item>
  <list-item>
    **Introduce simplified suppression scenarios.** Begin with fuel removal, then moisture or retardant treatments. Add resource allocation only once the interventions themselves have empirical support.
  </list-item>
</list>

## 8. Where to obtain the additional data

| Data source | What it contributes |
|---|---|
| <Link url="https://firms.modaps.eosdis.nasa.gov/" title="NASA FIRMS"/> | MODIS and VIIRS active-fire detections, radiative power, timestamps |
| <Link url="https://www.landfire.gov/" title="LANDFIRE"/> | Surface fuel models, canopy cover, density, base height, height |
| <Link url="https://www.earthdata.nasa.gov/data/missions/gedi" title="NASA GEDI"/> | Sampled three-dimensional forest structure |
| <Link url="https://apps.nationalmap.gov/downloader/" title="USGS 3DEP"/> | Elevation models and available airborne LiDAR |
| <Link url="https://rapidrefresh.noaa.gov/hrrr/" title="NOAA HRRR"/> | High-frequency meteorological forecasts |
| <Link url="https://cds.climate.copernicus.eu/" title="Copernicus ERA5"/> | Historical atmospheric reconstructions |
| <Link url="https://data-nifc.opendata.arcgis.com/" title="NIFC Open Data"/> | Incident-related geospatial records and perimeters |
| <Link url="https://www.mtbs.gov/" title="Monitoring Trends in Burn Severity"/> | Historical burned-area and severity products |

For training, I would prioritize **high-quality, time-sequenced fire perimeters** over simply collecting a larger number of final burned-area maps. A final perimeter tells you where a fire ended; multiple intermediate perimeters tell you how it evolved.

## Final recommendation

If I were allocating substantial supercomputing resources to this research, I would use:

- **ELMFIRE as the principal forecasting engine.**
- **WRF-SFIRE as the higher-fidelity coupled simulation engine.**
- **MODIS and VIIRS as complementary observation sources**, rather than treating them as exact fire boundaries.
- **LANDFIRE augmented by GEDI and airborne LiDAR**, where available, for fuel structure.
- **A learned correction and data-assimilation layer**, trained against historical time-dependent observations.
- **Moisture- and fuel-based intervention models** as the first step toward evaluating suppression.

The strongest research hypothesis to test is not necessarily that a neural network can replace the fire-spread physics. It is that **a conventional physics-based fire simulator, corrected with current satellite observations and learned environmental adjustments, can forecast active fires more accurately than either the unmodified simulator or an entirely data-driven model**.

A successful result would also have significant practical value: a model that continuously updates its predicted perimeter, quantifies uncertainty, and compares the likely effects of hypothetical interventions. Until those forecasts and intervention effects are independently validated, however, they should be treated as research outputs rather than tactical guidance for active firefighting.