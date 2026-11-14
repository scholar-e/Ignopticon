# Research Report: State of Wildfire Simulation and Prediction Models

<text color="secondary" size="sm">Research date: October 28, 2026 | Scope: Wildfire spread simulation, active-fire forecasting, software integration, predictive accuracy, and suppression modeling</text>

## 1. Executive summary

There is no single wildfire simulator that is best across all applications. The leading systems fall into three broad categories:

1. **Operational fire-spread models**, designed to forecast where wildfires will spread over hours or days.
2. **Research and physics-based models**, designed to capture fire dynamics more realistically, sometimes including interactions among wind, terrain, vegetation, and fire.
3. **Strategic and probabilistic simulators**, designed to evaluate wildfire risk, fuel treatments, or suppression strategies across many possible scenarios.

The most important finding is that **the best model for forecasting a wildfire already in progress is not necessarily the best model for predicting how a firefighting intervention will change its course**.

For example, Cell2Fire is computationally efficient and adaptable, but it was originally developed primarily for landscape-scale fire simulation and management planning. Technosylva's Wildfire Analyst is more directly suited to operational fire monitoring. QUIC-Fire and WRF-Fire offer more sophisticated representations of physical processes, but require more data and computational resources.

### Leading candidates by use case

| Use case | Strong candidates | Primary reason |
|---|---|---|
| Open-source, scalable simulation | Cell2Fire / C2F-W | Fast landscape simulation, flexible spread models |
| Open-source active-fire forecasting | ELMFIRE, FARSITE | Time-dependent fire growth and geospatial outputs |
| Operational incident forecasting | Technosylva Wildfire Analyst, WFDSS/FSPro | Integrated operational workflows and mapping |
| Physics-informed fire–wind interaction | WRF-Fire / WRF-SFIRE | Coupled atmospheric and fire dynamics |
| Fine-scale fire and vegetation dynamics | QUIC-Fire | Three-dimensional wind, fuel, and fire interactions |
| Fuel treatment and prevention analysis | IFTDSS, FlamMap | Established comparison of treatment scenarios |
| Evaluating suppression interventions | ELMFIRE, QUIC-Fire, custom Cell2Fire extensions | Different levels of support for intervention modeling |

These are capability-based selections, not a validated overall accuracy ranking.

### Critical qualification concerning accuracy

Published studies report encouraging results for individual systems, but **there is no defensible universal accuracy percentage**.

As one example, a 2025 Cell2Fire study improved the F1 score against the observed 2001 Dogrib Fire footprint from 0.74 for the Prometheus comparison to 0.83 for an optimized Cell2Fire configuration. That is meaningful evidence of potential performance, but it is not proof that Cell2Fire predicts 83% of all wildfires correctly. <Cite ref="turn464947search9"/>

A separate operational evaluation of Wildfire Analyst in California concluded that its automated fire-spread predictions were sufficiently accurate to support real-time initial-attack assessment, while identifying systematic model biases in some vegetation types. <Cite ref="turn449544search12"/>

The most promising direction for accurately describing ongoing wildfires is therefore to combine a fire-spread engine with current satellite, aerial, and ground observations, update its starting perimeter and environmental inputs, and generate multiple forecast scenarios rather than relying on one deterministic projection.

---

## 2. Comparison of major systems

The following ratings are qualitative assessments of documented functionality, not benchmark scores. UI integration describes how conveniently users can operate and visualize the model; suppression support refers specifically to the ability to represent firefighting actions rather than merely display fire behavior.

| System | Source availability | UI integration | Active-fire suitability | Suppression potential |
|---|---|---|---|---|
| Cell2Fire | Open source | Low–moderate | Moderate with adaptation | Fuel modifications, custom extensions |
| C2F-W | Open source | High via QGIS | Moderate | Fuel modifications |
| FARSITE / FlamMap | Free software | Desktop GUI, GIS export | High | Barriers, fuel treatments |
| ELMFIRE | Open source | CLI and GIS outputs | High | Explicit suppression modeling |
| WRF-Fire | Open source | Technical tools, custom GIS | High in research workflows | Indirect, requires adaptation |
| QUIC-Fire | Research-oriented | Technical workflows | Moderate; developing | Fuel breaks and fire interactions |
| WFDSS / FSPro | Restricted operational access | High, web-based | High for strategic forecasts | Limited barriers and masks |
| IFTDSS | Public web application | High, web-based | Mainly planning | Strong fuel-treatment comparison |
| Technosylva Wildfire Analyst | Commercial | Very high, web/mobile | Very high | Operational scenario planning |

An important distinction: sophisticated interfaces do not necessarily mean sophisticated underlying physics. A well-integrated system can run relatively simple spread equations with excellent situational data, while a physically detailed research model can remain difficult to deploy operationally.

## 3. Detailed model evaluations

### 3.1 <Entity category="software" value="Cell2Fire" disambig="Open-source wildfire growth simulation framework"/>

<row align="start" gap={3}>
  <box flex="1">
    **Best for:** large-scale simulations, research, wildfire risk analysis, and experimentation with custom fire-spread algorithms.

    **Architecture:** C++ simulation engine with Python orchestration and postprocessing. It partitions the landscape into cells and propagates fire according to directional rates of spread and an elliptical growth approximation.

    **Interfaces:** primarily CLI and Python; visualizations include growth grids, statistical plots, and burn-probability maps.
  </box>
  <box width="156px">
    <AsyncImage query="Cell2Fire wildfire simulator cell grid fire growth visualization burn probability map" aspectRatio="4:3"/>
    <caption>Representative simulation visualization</caption>
  </box>
</row>

Cell2Fire separates the *spread-rate model* from the *spatial fire propagation engine*. This is important because its behavior can be adapted to different regional models rather than being permanently tied to one fire-behavior formulation.

**Required inputs**

| Input | Details |
|---|---|
| Fuel classification | Raster/grid identifying vegetation and fuel type |
| Weather | Wind, temperature, relative humidity, and regional fire-weather parameters |
| Fuel moisture | Direct or derived moisture variables appropriate to the selected spread model |
| Topography | Elevation, slope, and spatial alignment |
| Ignition | Starting location, time, or scenario definition |
| Model configuration | Cell size, time period, simulation count, spread parameters |

Its original implementation is particularly aligned with the Canadian Fire Behavior Prediction system, although subsequent research integrates models including Behave/Rothermel and KITRAL.

**Accuracy and validation.** The July 2025 *Scientific Reports* paper by Kim, Pais, and Gonzalez demonstrated that an optimized Cell2Fire configuration achieved an F1 score of 0.83 against the Dogrib Fire's observed burn footprint, compared with 0.74 for Prometheus. This optimization used historical fire evidence to tune parameters, so the result should be regarded as a calibrated reconstruction benchmark rather than a prospective operational forecast. <Cite refs={["turn464947search11","turn464947search9"]}/>

**Active-fire limitations.** Cell2Fire can simulate growth from a supplied fire state, but the original package does not provide an integrated operational pipeline for ingesting active-fire observations, reconciling successive perimeter measurements, and continuously issuing updated forecasts.

**Suppression support.** It can support landscape interventions by modifying the fuel grid. For example, a prescribed fuel reduction or clearing can be encoded as a changed cell state. However, realistic crew deployment, aerial suppression, line-construction productivity, and the probability of successful containment require additional modeling.

**Verdict:** a strong base for a modifiable wildfire simulation project, but not a turnkey active-fire prediction platform.

Resources: <Link url="https://github.com/cell2fire/Cell2Fire" title="Original repository"/> · <Link url="https://www.nature.com/articles/s41598-025-05706-6" title="2025 validation study"/>

### 3.2 <Entity category="software" value="Cell2Fire-World (C2F-W)" disambig="QGIS-integrated wildfire simulator"/>

<row align="start" gap={3}>
  <box flex="1">
    **Best for:** a Cell2Fire-derived simulation engine that is easier to use with geographic data.

    C2F-W extends the original framework to support three fire-behavior model families: Canadian FBP, Chilean KITRAL, and Scott–Burgan fuel models. It provides a QGIS graphical interface.

    This is particularly relevant if the objective is to run an extensible engine from an interactive map without building every data-import and visualization component.
  </box>
  <box width="156px">
    <AsyncImage query="QGIS desktop software wildfire raster simulation map GIS interface" aspectRatio="4:3"/>
    <caption>GIS-based workflow example</caption>
  </box>
</row>

Its data requirements are broadly similar to Cell2Fire: fuel classification, terrain, weather, and initial fire conditions, with additional parameters depending on the selected spread model.

The principal advantage over the original Cell2Fire is workflow integration. Its QGIS approach gives users access to established tools for selecting locations, preparing rasters, modifying vegetation layers, and inspecting outputs.

However, a QGIS interface should not be confused with real-time incident monitoring. Operational ingestion, forecast scheduling, and uncertainty management would still need to be established.

**Verdict:** the most directly relevant Cell2Fire variant for a GIS-based application prototype.

Resources: <Link url="https://github.com/fire2a/C2F-W" title="C2F-W repository"/> · <Link url="https://fire2a.github.io/C2F-W/index.html" title="Project documentation"/> <Cite ref="turn464947search5"/>

### 3.3 <Entity category="software" value="ELMFIRE" disambig="Eulerian Level Set Model of Fire Spread"/>

**Best for:** open-source, operationally relevant fire-spread simulations with a pathway to suppression scenario modeling.

ELMFIRE uses a level-set numerical method to advance fire boundaries. Rather than simply deciding whether one cell ignites an adjacent cell, a level-set model represents a continuously evolving fire front on a computational grid.

Its current documentation describes additional functionality for crown fire, spotting, smoke, urban fire spread, Monte Carlo simulation, and suppression.

<box gap={2}>
  <text weight="medium" size="sm">Principal input data</text>
  | Input | Requirements |
  |---|---|
  | Terrain | Elevation, slope, aspect rasters |
  | Fuels | Fuel model raster and fuel definitions |
  | Canopy | Height, base height, bulk density, cover |
  | Moisture | Dead fuel at 1, 10, and 100 hours; live herbaceous and woody fuels |
  | Weather | Time-dependent wind speed and direction |
  | Active fire | Ignition or initialized fire geometry |
  | Advanced suppression | Suppression capacity, difficulty, potential control locations, or barriers |
</box>

ELMFIRE accepts standard GeoTIFF inputs, including compatible multiband LANDFIRE landscapes. Configurations are defined in a text input file; the engine supports parallel execution through MPI and can be run in Linux-based environments. <Cite refs={["turn449544search2","turn953742search1"]}/>

**UI integration:** the main interface is technical rather than a complete public-facing GIS dashboard. However, georeferenced raster output makes integration with QGIS, Python geospatial libraries, or a custom web map straightforward.

**Accuracy:** ELMFIRE documents verification cases and historical-fire validation workflows, but its published materials do not establish a single transferable forecast accuracy percentage. Its own documentation highlights the difficulty of comparing models evaluated on different historical fires and metrics. <Cite ref="turn953742search2"/>

**Suppression modeling:** a significant advantage. The documented spatially explicit extended-attack model can represent direct attacks on fireline segments, indirect attacks at favorable control locations, and finite suppression capacity. This is more sophisticated than simply declaring a prescribed firebreak to be an impassable barrier.

The model still represents suppression using abstractions. For example, fireline-building capacity is expressed as a rate, not a detailed simulation of every firefighting vehicle, crew, or aircraft.

**Verdict:** my first open-source candidate for building a system that forecasts active fires and compares alternative suppression policies.

Resources: <Link url="https://elmfire.io/" title="Official ELMFIRE documentation"/> · <Link url="https://elmfire.io/validation.html" title="Validation documentation"/>

### 3.4 <Entity category="software" value="FARSITE / FlamMap" disambig="US Forest Service fire behavior modeling software"/>

<row align="start" gap={3}>
  <box flex="1">
    **Best for:** established wildfire-spread calculations, operationally familiar model outputs, and compatibility with U.S. fuel datasets.

    FlamMap includes static fire-behavior analysis, Minimum Travel Time calculations, burn-probability analysis, and FARSITE-based time-dependent fire growth.

    Its USFS documentation currently lists a FlamMap 6.2 release from October 2026.
  </box>
  <box width="156px">
    <AsyncImage query="FlamMap FARSITE wildfire simulation software fire growth outputs map screenshot" aspectRatio="4:3"/>
  </box>
</row>

**Required inputs:** a landscape file containing terrain and fuel layers; initial fuel moisture; weather streams for FARSITE; and ignition geometry. Canopy characteristics and custom wind/fuel layers improve coverage of particular fire behaviors.

The standard landscape model includes eight geospatial layers: elevation, slope, aspect, fuel model, canopy cover, canopy height, canopy base height, and canopy bulk density. <Cite refs={["turn449544search4","turn449544search5"]}/>

**UI integration:** FlamMap is a Windows desktop application with modeling dialogs and GIS export capabilities. It is not a modern browser-based map application and does not have the seamless map-centric experience of WFDSS or Technosylva.

**Active-fire accuracy:** FARSITE is an important reference model for operational fire-growth calculations, but the quality of its forecast depends substantially on fuel maps, wind, fuel moisture, and correctly initialized fire perimeters. A model's use by agencies is evidence of operational relevance, not proof of superiority in every fire environment.

**Suppression:** barriers and fuel changes can be represented. There is not a comparable built-in resource-allocation simulator for detailed firefighter tactics.

**Verdict:** an important baseline model to include in comparative experiments, particularly in the United States.

Resource: <Link url="https://research.fs.usda.gov/firelab/products/dataandtools/flammap" title="USFS FlamMap page"/>

### 3.5 <Entity category="software" value="WRF-Fire / WRF-SFIRE" disambig="Coupled Weather Research and Forecasting wildfire model"/>

**Best for:** studying wildfires where fire–atmosphere interactions materially affect fire behavior.

A distinguishing characteristic is two-way coupling:

<box border radius="lg" padding={3} gap={2}>
  <svg viewBox="0 0 330 150" width="100%" xmlns="http://www.w3.org/2000/svg">
    <rect x="8" y="34" width="136" height="76" rx="10" fill="#D9E8F7" stroke="#618BB5"/>
    <rect x="186" y="34" width="136" height="76" rx="10" fill="#F9E4D8" stroke="#C7865D"/>
    <g fill="#203D5B" fontSize="13" textAnchor="middle" fontWeight="600">
      <text x="76" y="64">Atmospheric</text>
      <text x="76" y="81">model</text>
    </g>
    <g fill="#7C432B" fontSize="13" textAnchor="middle" fontWeight="600">
      <text x="254" y="64">Fire spread</text>
      <text x="254" y="81">model</text>
    </g>
    <path d="M145 54 L182 54 M174 48 L182 54 L174 60" stroke="currentColor" fill="none" strokeWidth="2"/>
    <path d="M186 90 L148 90 M156 84 L148 90 L156 96" stroke="currentColor" fill="none" strokeWidth="2"/>
    <text x="165" y="24" textAnchor="middle" fontSize="10" fill="currentColor">Wind drives fire</text>
    <text x="165" y="128" textAnchor="middle" fontSize="10" fill="currentColor">Heat alters airflow</text>
  </svg>
  <caption textAlign="center">Two-way fire–atmosphere coupling, simplified</caption>
</box>

In ordinary spread models, wind is often an external input. In WRF-Fire, heat released by the fire can affect atmospheric motion, which in turn influences the fire's subsequent evolution.

This is a valuable capability when topographic winds, convection, and fire-induced local atmospheric behavior are important.

**Required data:** atmospheric initial and boundary conditions, nested weather domains, fuel maps, high-resolution terrain, ignition geometry, and numerical model settings. WRF-Fire documentation explicitly distinguishes the high-resolution fuel and terrain inputs needed for the fire grid from the coarser atmospheric grids. <Cite ref="turn449544search17"/>

**UI integration:** primarily scientific computing, model configuration, NetCDF datasets, and postprocessing tools. A useful interactive UI requires substantial development.

**Validation:** a 2025 study evaluated observation-integration approaches for WRF-SFIRE using infrared and satellite information for the Caldor Fire, illustrating the importance of assimilating updated fire observations into coupled forecasting workflows. <Cite ref="turn449544search16"/>

**Suppression:** not a turnkey tactical suppression system. Custom fuel modifications, ignition changes, and model-level interventions are possible, but require substantial engineering.

**Verdict:** compelling for atmospheric research and unusually complex fire behavior, but generally a heavier starting point for an interactive operational platform.

Resource: <Link url="https://www2.mmm.ucar.edu/wrf/site/documentation/users_guide/fire.html" title="WRF-Fire user documentation"/>

### 3.6 <Entity category="software" value="QUIC-Fire" disambig="Los Alamos coupled fire and atmospheric simulation model"/>

**Best for:** detailed local fire dynamics, prescribed burning, vegetation and wind interactions, and experimental fuel-break behavior.

QUIC-Fire combines a three-dimensional wind solver with a physics-informed cellular-automata fire model. It occupies a useful middle ground between simpler spread models and computationally demanding, fully resolved combustion simulations.

**Required data:** three-dimensional or parameterized fuel distributions, vegetation characteristics, meteorological and wind conditions, terrain, ignition configuration, and simulation-domain parameters.

**UI integration:** the `quicfire-tools` Python package supports generating inputs and processing outputs, providing a foundation for custom visualization and automation. However, the QUIC-Fire engine itself is closed-source and requires appropriate access or licensing. The tooling package being open-source does not make the simulator open-source. <Cite ref="turn929708search9"/>

**Accuracy:** the 2026 Camp Swift evaluation compared QUIC-Fire with several Fire Dynamics Simulator configurations using controlled burn observations. It found that QUIC-Fire substantially overpredicted head-fire arrival times in those experiments—meaning the modeled fire arrived too late. All models in the comparison had important shortcomings in representing some vegetation effects and fire shapes. <Cite ref="turn929708search8"/>

This is particularly important: even a model with richer three-dimensional processes can perform worse than another model on an operationally critical metric.

**Suppression relevance:** QUIC-Fire is useful for exploring how fire interacts with fuel breaks, vegetation treatments, wind fields, and planned ignition patterns. It is less suited to directly estimating how many crews or aircraft would be required to contain a large incident.

**Verdict:** a strong physics-oriented research platform, but with licensing and validation constraints that make it less immediately attractive than ELMFIRE for a broadly accessible application.

Resource: <Link url="https://pypi.org/project/quicfire-tools/" title="QUIC-Fire Python tooling"/>

### 3.7 <Entity category="software" value="Wildfire Analyst" disambig="Technosylva operational wildfire prediction platform"/> — Technosylva

<row align="start" gap={3}>
  <box flex="1">
    **Best for:** incident-centric mapping, operational forecasts, and live situational awareness.

    The platform integrates fire simulation with a map-based operating environment. Its mobile product supports active IRWIN incidents, ALERTWildfire cameras, on-demand simulations, and map visualization.

    The commercial platform offers FireSim and FireCast configurations, including the ability to compare simulations on a timeline.
  </box>
  <box width="156px">
    <AsyncImage query="Technosylva Wildfire Analyst wildfire operations simulation map interface" aspectRatio="4:3"/>
  </box>
</row>

FireSim supports deterministic and probabilistic runs. Its documented probabilistic mode performs 50 simulations using variations in wind speed, wind direction, and selected moisture parameters. Its forecast-weather mode can use hourly WRF forecast fields. <Cite ref="turn929708search2"/>

**Data requirements:** the user-facing workflow primarily exposes ignition or incident location, simulation duration, weather configuration, fuel scenario, and probabilistic parameters. Much of the geospatial and meteorological preprocessing is handled by the platform.

**Accuracy:** a published assessment of automated Wildfire Analyst simulations against FireGuard observations in California found results suitable for operational initial-attack assessment, although predicted rates of spread showed environmental and fuel-type biases. This supports its operational usefulness, but the findings should not be generalized into a guarantee for every incident. <Cite ref="turn449544search12"/>

**Suppression:** the platform can support operational decision-making and what-if comparisons, but its public documentation does not establish a generally available tactical simulator that directly predicts the outcomes of specific crew or aircraft deployment plans.

**Verdict:** among the strongest UI and workflow references for a live wildfire prediction product, especially when ease of incident use matters more than access to the underlying model source code.

Resources: <Link url="https://helpcenter.technosylva.com/wildfire-operations/simulations" title="Simulation interface documentation"/> · <Link url="https://helpcenter.technosylva.com/wfa-mobile/about" title="Mobile application capabilities"/>

### 3.8 WFDSS, FSPro, and IFTDSS

These are best understood as **decision-support environments**, rather than three interchangeable standalone fire-spread engines.

<box gap={3}>
  <row align="start" gap={3}>
    <box flex="1" gap={1}>
      **<Entity category="software" value="WFDSS" disambig="U.S. Wildland Fire Decision Support System"/> / FSPro**

      U.S. federal, map-centric operational decision support. FSPro estimates the probability of fire reaching different locations over longer forecast periods, commonly beyond five days.

      It accepts a current fire perimeter or ignition and evaluates future growth across weather scenarios. By design, its principal forecasts assume no suppression, although some barriers and masks can be represented.

      Access is restricted to authorized wildland-fire personnel with appropriate roles. <Cite refs={["turn449544search1","turn449544search9"]}/>
    </box>
    <AsyncImage query="WFDSS FSPro wildfire burn probability map United States decision support" aspectRatio="4:3" maxWidth="140px"/>
  </row>
  <divider color="subtle"/>
  <row align="start" gap={3}>
    <box flex="1" gap={1}>
      **<Entity category="software" value="IFTDSS" disambig="Interagency Fuel Treatment Decision Support System"/>**

      A publicly accessible web application built primarily for fuels management and treatment planning.

      It provides U.S. reference data, map visualization, model execution, and comparison of hypothetical treatments such as thinning, clearing, and prescribed burning.

      It is particularly useful for assessing how changes in landscape fuels may influence future fire behavior, but it is not primarily a continuously updated active-incident forecasting service. <Cite ref="turn449544search0"/>
    </box>
    <AsyncImage query="IFTDSS wildfire fuel treatment planning web map interface" aspectRatio="4:3" maxWidth="140px"/>
  </row>
</box>

Resources: <Link url="https://wfdss.firenet.gov/" title="WFDSS"/> · <Link url="https://iftdss.firenet.gov/" title="IFTDSS"/>

---

## 4. What input data do these systems actually require?

A practical distinction is between the *minimum data required for a model to run* and the *data required to produce a useful forecast*.

A simulator can often run with simplified fuels, uniform weather, and one ignition point. That does not mean it can reliably forecast a real wildfire with those inputs.

| Data category | Basic simulation | Useful active-fire forecast | Primary problem if inaccurate |
|---|---|---|---|
| Terrain | DEM and slope | High-resolution terrain | Incorrect topographic spread |
| Vegetation | Fuel type | Updated fuel structure, canopy, treatments | Wrong spread speed/intensity |
| Wind | Single speed/direction | Hourly, spatially varying forecasts | Wrong fire direction and growth |
| Moisture | Fixed estimates | Observations and updated estimates | Wrong ignition/spread behavior |
| Current fire | Ignition point | Timestamped perimeter and active fronts | Incorrect initialization |
| Spotting | Optional | Calibrated ember transport assumptions | Missed fires beyond the main front |
| Suppression | Usually absent | Control lines, tactics, timing, resources | Unrealistic future growth |
| Observations | Optional | Repeated satellite, aerial and ground updates | Error accumulates over time |

### Key public data sources for U.S. deployments

<box gap={2}>
  <row align="start" gap={2}>
    <icon name="map" color="secondary"/>
    <box flex="1" gap=0>
      **<Link url="https://landfire.gov/" title="LANDFIRE"/>**

      Vegetation, fuel models, canopy information, and derived landscape datasets. A natural starting point for FARSITE- and ELMFIRE-compatible geospatial inputs.
    </box>
  </row>
  <row align="start" gap={2}>
    <icon name="satellite" color="secondary"/>
    <box flex="1" gap=0>
      **<Link url="https://firms.modaps.eosdis.nasa.gov/" title="NASA FIRMS"/>**

      MODIS and VIIRS active-fire detections, useful for monitoring and updating the state estimate. Thermal detections are not the same as a surveyed perimeter.
    </box>
  </row>
  <row align="start" gap={2}>
    <icon name="cloud-sun" color="secondary"/>
    <box flex="1" gap=0>
      **<Link url="https://rapidrefresh.noaa.gov/hrrr/" title="NOAA HRRR"/>**

      Frequently refreshed weather forecasts, including wind fields. These still need conversion and downscaling appropriate to the fire model and terrain.
    </box>
  </row>
  <row align="start" gap={2}>
    <icon name="mountain" color="secondary"/>
    <box flex="1" gap=0>
      **<Link url="https://apps.nationalmap.gov/downloader/" title="USGS National Map"/>**

      High-resolution elevation data for calculating topographic inputs.
    </box>
  </row>
  <row align="start" gap={2}>
    <icon name="flame" color="secondary"/>
    <box flex="1" gap=0>
      **<Link url="https://data-nifc.opendata.arcgis.com/" title="NIFC open data"/>**

      Incident-related geospatial information, including available fire perimeter datasets. Update frequency, observation timestamps, and source reliability vary.
    </box>
  </row>
</box>

For a model expected to accurately describe a fire in progress, observational latency and timestamps are as important as spatial resolution. A precisely measured perimeter from several hours ago may be much less useful than a recent, somewhat uncertain estimate during fast-moving fire conditions.

## 5. How accurate are current wildfire simulators?

Accuracy should be divided into at least four distinct quantities: final burned area, geographic overlap, fire arrival time, and predicted fire intensity.

A simulator can accurately estimate total burned acreage while placing the fire in the wrong geographic location. It can also reproduce a final burn perimeter reasonably well while making substantial errors in *when* the fire reaches particular locations.

### Published empirical results

| Model/study | Evaluation | Reported result | Interpretation |
|---|---|---|---|
| Cell2Fire, 2025 | 2001 Dogrib Fire | F1 = 0.83 after optimization; Prometheus comparison F1 = 0.74 | Good historical reconstruction with calibration |
| Wildfire Analyst, California study | Automated spread estimates vs. FireGuard observations | Authors found accuracy adequate for initial-attack use | Supports short-term operational application, with biases |
| QUIC-Fire, 2026 | Three Camp Swift experimental burn blocks | Substantial overprediction of fire arrival times | Physics-informed modeling still has significant errors |
| FDS variants, 2026 | Same Camp Swift experiments | Some configurations outperformed QUIC-Fire on arrival time | Relative accuracy depends on model configuration and metric |
| ELMFIRE | Automated and historical-fire validation framework | No single comparable aggregate score established here | Requires incident- and horizon-specific evaluation |

Sources: <Cite refs={["turn464947search9","turn449544search12","turn929708search8","turn953742search2"]}/>

One particularly useful source is the August 2026 review *Towards a fit-for-purpose fire simulator ecosystem*. It emphasizes that model performance should be evaluated according to the specific problem being solved, rather than relying on a universal claim of model superiority. <Cite ref="turn929708search1"/>

### How model accuracy should be measured

For a new system, I would use these principal metrics.

**1. Geographic overlap (Intersection over Union)**

\[
IoU=\frac{|A_{\mathrm{pred}}\cap A_{\mathrm{obs}}|}{|A_{\mathrm{pred}}\cup A_{\mathrm{obs}}|}
\]

Here, the predicted burned area is compared with observed burned area at a matching timestamp.

**2. Burned-area F1 score**

\[
F_1=\frac{2TP}{2TP+FP+FN}
\]

This measures how accurately predicted burned and unburned regions agree with observations. Both IoU and F1 should be reported, with raster resolution held constant.

**3. Fire arrival-time error**

\[
MAE_t=\frac{1}{N}\sum_{i=1}^{N}|\hat t_i-t_i|
\]

This measures the average difference between predicted and actual fire arrival times at observed locations.

**4. Probabilistic calibration**

For ensemble models, compare predicted burn probabilities against actual event frequencies. For example, among independently tested locations assigned a 70% probability of burning within 24 hours, approximately 70% should burn if the probabilities are well calibrated.

A rigorous benchmark would use held-out fires, multiple vegetation types, different weather conditions, and fixed forecast horizons such as 1, 3, 6, 12, and 24 hours. Historical data used for calibration should be separated from forecast evaluation.

### Which conditions remain especially difficult?

Spot fires, unexpected wind changes, active crown fire, abrupt changes in fuel continuity, and suppression activities create major forecasting challenges.

A 2025 systematic review of 102 studies on wildfire spotting found important remaining research gaps in comprehensive physics-based ember-generation, transport, and ignition modeling, particularly in operational implementations. <Cite ref="turn929708search10"/>

Consequently, accuracy under ordinary surface-fire conditions should not be taken as evidence of equivalent performance in extreme wind-driven events.

---

## 6. Can these models describe a wildfire already in progress?

Yes, but there are several materially different levels of capability.

<box border radius="lg" padding={3} gap={3}>
  <text weight="medium" size="sm">Operational sophistication, from lowest to highest</text>
  <list marker="number" connector="solid" gap={2}>
    <list-item>
      **Static simulation from an ignition point**

      Uses a single starting point and chosen weather conditions. Suitable for hypotheticals, but rapidly diverges from an uncontrolled real fire.
    </list-item>
    <list-item>
      **Simulation initialized from a known perimeter**

      Starts from observed fire geometry. Much better for established incidents, provided the perimeter timestamp is reliable.
    </list-item>
    <list-item>
      **Continuously reinitialized forecasting**

      Periodically updates the simulated fire state using new perimeter and meteorological observations, then issues revised forecasts.
    </list-item>
    <list-item>
      **Data-assimilating ensemble forecasting**

      Combines multiple plausible fire states, observation uncertainty, weather uncertainty, and model uncertainty into probabilistic forecasts.
    </list-item>
  </list>
</box>

Most standalone simulation engines primarily implement the first two approaches. A workflow that continuously refreshes and corrects its simulated fire state generally needs additional orchestration, observation processing, and data-assimilation components.

### Recommended architecture for active-fire forecasting

<box border radius="lg" padding={3} gap={1}>
  <svg viewBox="0 0 340 396" width="100%" xmlns="http://www.w3.org/2000/svg">
    {#each [{x:8,t:"Fire perimeters",s:"Satellite / aircraft / ground"},{x:119,t:"Environment",s:"Weather, terrain, fuel"},{x:230,t:"Operations",s:"Lines, tactics, resources"}] as d}
      <rect x={d.x} y="9" width="102" height="58" rx="7" fill="#E6EEF7" stroke="#9DB7D1"/>
      <text x={d.x+51} y="30" fontSize="10.5" fontWeight="600" textAnchor="middle" fill="#243D59">{d.t}</text>
      <text x={d.x+51} y="44" fontSize="7.2" textAnchor="middle" fill="#48647E">{d.s.split(",")[0]}</text>
      <text x={d.x+51} y="54" fontSize="7.2" textAnchor="middle" fill="#48647E">{d.s.split(",").slice(1).join(",")}</text>
      <path d={`M ${d.x+51} 67 V 86`} stroke="currentColor" strokeWidth="1.4" fill="none"/>
    {/each}
    <rect x="42" y="86" width="256" height="53" rx="8" fill="#DCECE6" stroke="#8EBAA9"/>
    <text x="170" y="107" textAnchor="middle" fontSize="13" fontWeight="600" fill="#255846">Data preparation and assimilation</text>
    <text x="170" y="123" textAnchor="middle" fontSize="10" fill="#3E6C5A">Align observations, initialize current fire state</text>
    <path d="M170 139 V160 M165 154 L170 160 L175 154" stroke="currentColor" strokeWidth="1.5" fill="none"/>
    <rect x="42" y="160" width="256" height="62" rx="8" fill="#E6EEF7" stroke="#9DB7D1"/>
    <text x="170" y="182" textAnchor="middle" fontSize="13" fontWeight="600" fill="#243D59">Simulation ensemble</text>
    <text x="170" y="198" textAnchor="middle" fontSize="10" fill="#48647E">ELMFIRE / FARSITE / Cell2Fire</text>
    <text x="170" y="211" textAnchor="middle" fontSize="9.5" fill="#48647E">Weather and suppression scenarios</text>
    <path d="M170 222 V242 M165 236 L170 242 L175 236" stroke="currentColor" strokeWidth="1.5" fill="none"/>
    <rect x="42" y="242" width="256" height="66" rx="8" fill="#F6E9D7" stroke="#D9B77D"/>
    <text x="170" y="263" textAnchor="middle" fontSize="13" fontWeight="600" fill="#7A5526">Forecast and comparison</text>
    <text x="170" y="279" textAnchor="middle" fontSize="10" fill="#876638">Burn probability • arrival time</text>
    <text x="170" y="293" textAnchor="middle" fontSize="10" fill="#876638">Intensity • intervention differences</text>
    <path d="M170 308 V328 M165 322 L170 328 L175 322" stroke="currentColor" strokeWidth="1.5" fill="none"/>
    <rect x="42" y="328" width="256" height="57" rx="8" fill="#DCECE6" stroke="#8EBAA9"/>
    <text x="170" y="349" textAnchor="middle" fontSize="13" fontWeight="600" fill="#255846">Interactive decision interface</text>
    <text x="170" y="365" textAnchor="middle" fontSize="10" fill="#3E6C5A">Map, timeline, uncertainty, scenarios</text>
  </svg>
</box>

A critical implementation detail is to separate the *observed fire state* from the *modeled fire state*. The UI should never present an unobserved forecast perimeter as though it were a confirmed active-fire perimeter.

---

## 7. Can they predict the results of firefighting operations?

This is a more difficult and less mature application than ordinary fire-spread forecasting.

It helps to distinguish three levels of simulation.

| Suppression capability | Example | What it can answer |
|---|---|---|
| Modified landscape | Cell2Fire, IFTDSS | What if vegetation is removed or treated here? |
| Fireline and containment abstractions | ELMFIRE, FSPro barriers | How might a control feature or suppression capacity change fire growth? |
| Detailed resource and tactical simulation | Specialized research/custom systems | What happens if particular crews or aircraft execute particular tactics? |

**The third level is the least well established** in general-purpose publicly available wildfire simulators.

### Why ELMFIRE is particularly relevant

ELMFIRE's documented suppression functionality provides a useful intermediate level.

Its spatially explicit model includes:

- Available suppression capacity, expressed as fireline length per hour.
- Deployment timing.
- Direct attack on active fireline segments.
- Indirect attack at favorable potential control locations.
- Local conditions affecting containment effectiveness.
- Probabilistic failure of control locations.

These are documented capabilities, not merely proposed future extensions. <Cite ref="turn953742search0"/>

For example, one could evaluate several hypothetical policies in which suppression capacity, activation time, or control-line placement varies, while holding sampled weather and fuel scenarios constant.

The outputs could then be compared using final burned area, burn probability near assets, fire arrival time, and modeled containment probability.

However, the validity of those comparisons depends on how accurately the suppression parameters represent real operations.

### The major limitation: counterfactual validation

Suppose a fire is contained after aircraft make several retardant drops. There is no directly observable version of that same fire in which the aircraft never arrived.

That means the efficacy of an intervention cannot be established simply by comparing the model's suppressed forecast with the fire's observed outcome.

A proper evaluation would need historical suppression records, matched scenarios, controlled experimental burns where appropriate, and calibrated estimates of effectiveness. Confounding factors such as wind changes, fire intensity, accessibility, and resource allocation must also be considered.

Thus, an apparently precise claim such as “this deployment reduces burned acreage by 35%” is not scientifically justified without a validated intervention model and uncertainty analysis.

---

## 8. Demonstration: what a probabilistic suppression comparison would look like

The following chart is an **illustrative synthetic example**, not an ELMFIRE run or real wildfire forecast. It demonstrates a comparison that a well-designed simulation interface should support.

{@body const scenarios = [{name:"No suppression",data:[0,8,24,54,112,205,342]},{name:"Early control effort",data:[0,8,20,37,59,86,119]},{name:"Delayed control effort",data:[0,8,24,54,88,126,177]}]}
<Chart content={{"chartType":"line","meta":{"title":"Illustrative wildfire growth under three strategies","description":"Synthetic scenario, not an operational prediction."},"xKey":"hour","series":[{"dataKey":"baseline","label":"No suppression","valueSuffix":" ha"},{"dataKey":"early","label":"Early control effort","valueSuffix":" ha"},{"dataKey":"delayed","label":"Delayed control effort","valueSuffix":" ha"}],"yAxisMin":0,"yAxisMax":400,"yAxisLabel":"Cumulative burned area (hectares)","xAxisLabel":"Hours after initialization","data":[{"hour":0,"baseline":0,"early":0,"delayed":0},{"hour":1,"baseline":8,"early":8,"delayed":8},{"hour":2,"baseline":24,"early":20,"delayed":24},{"hour":3,"baseline":54,"early":37,"delayed":54},{"hour":4,"baseline":112,"early":59,"delayed":88},{"hour":5,"baseline":205,"early":86,"delayed":126},{"hour":6,"baseline":342,"early":119,"delayed":177}]}}/>

The correct operational product would not show only three smooth trajectories. It would provide uncertainty bands for each scenario, probability of control-line breach, and spatial maps displaying which locations are likely to be affected.

Ideally, it would also distinguish between a *change in predicted fire growth* and a *change in expected losses*. Preventing fire from reaching one densely populated area may be more valuable than preventing a larger burned area elsewhere.

---

## 9. Recommended implementation paths

<box gap={3}>
  <row align="start" gap={3}>
    <box flex="1" gap={1}>
      <title size="lg">A. Active wildfire forecasting application</title>
      <text color="secondary" size="xs">Best starting engine: ELMFIRE</text>
      Use ELMFIRE with standard fuel and topography data, updated weather forecasts, and timestamped observed fire perimeters.

      A custom web interface could display fire arrival time, burn probability, projected perimeter, and forecast uncertainty.

      This offers a useful combination of extensibility, model functionality, and operationally relevant outputs.
    </box>
    <badge color="success">Preferred</badge>
  </row>
  <divider color="subtle"/>
  <box gap={1}>
    <title size="lg">B. Researching new fire-spread algorithms</title>
    <text color="secondary" size="xs">Best starting engine: Cell2Fire / C2F-W</text>
    These are attractive when the principal objective is to modify the spread algorithm, introduce regional models, test ensembles, or optimize large numbers of hypothetical scenarios.

    C2F-W provides an existing QGIS interface that reduces the amount of GIS tooling needed at the outset.
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <title size="lg">C. Modeling firefighting effectiveness</title>
    <text color="secondary" size="xs">Best starting engine: ELMFIRE suppression module</text>
    Start with its spatial suppression abstractions and evaluate how intervention timing, capacity, and location change simulated outcomes.

    Add more detailed resource models only after the basic fire-spread and containment predictions have been validated. This avoids accumulating uncertainty across multiple poorly tested submodels.
  </box>
  <divider color="subtle"/>
  <box gap={1}>
    <title size="lg">D. Immediate operational interface with minimal engineering</title>
    <text color="secondary" size="xs">Best reference: Technosylva Wildfire Analyst</text>
    Its existing incident, timeline, scenario comparison, and mobile workflows make it a useful benchmark for interface design.

    Commercial access and limited transparency into proprietary components are the primary disadvantages for independent research.
  </box>
</box>

### Suggested software stack for a custom system

| Layer | Suggested component |
|---|---|
| Simulation engine | ELMFIRE, with FARSITE as a comparator |
| Data preparation | Python, GDAL, rasterio, GeoPandas |
| Forecast orchestration | Containerized jobs and a queue/worker system |
| Scenario storage | PostgreSQL/PostGIS and geospatial object storage |
| Backend API | FastAPI or equivalent |
| Interactive map | MapLibre GL JS |
| GIS authoring | QGIS |
| Validation | Historical perimeter datasets and reproducible evaluation scripts |
| Forecast updates | Scheduled observation ingestion and model reinitialization |

These are engineering recommendations, not claims that the individual packages already constitute an integrated, production-ready system.

---

## 10. Research priorities and major unresolved problems

A future wildfire forecasting system would benefit most from tackling the following challenges, ordered by my assessment of their practical importance.

1. **Observation-driven correction:** frequent and reliable updates to fire boundaries, including uncertainty from incomplete aerial and satellite coverage.
2. **Fuel-data quality:** better representation of recent treatments, canopy characteristics, disturbances, and changes not yet reflected in standard fuel products.
3. **Localized wind estimation:** especially in mountainous terrain and during rapidly changing wind conditions.
4. **Spotting and extreme fire behavior:** modeling new ignitions beyond the primary front without systematically overestimating or underestimating their occurrence.
5. **Suppression effectiveness:** estimating the probability that specific containment tactics succeed under different weather, fire-intensity, terrain, and resource conditions.
6. **Prospective validation:** evaluating forecasts generated using only information available at the actual forecast issuance time, not retrospectively optimized inputs.

A further research opportunity is **multi-model ensembles**: comparing or combining fast operational spread models with selected physically sophisticated models. However, a multi-model average is not automatically better; model errors can be correlated, and ensemble weights must be calibrated on independent data.

---

## 11. Principal research references

The following are particularly useful starting points for a deeper technical investigation.

| Publication or source | Relevance |
|---|---|
| <Link url="https://www.nature.com/articles/s41598-025-05706-6" title="Kim et al. (2025), Fire spread simulations using Cell2Fire"/> | Cell2Fire validation, calibration, and computational performance |
| <Link url="https://link.springer.com/article/10.1186/s42408-026-00566-5" title="Clarke et al. (2026), Towards a fit-for-purpose fire simulator ecosystem"/> | Broad assessment of model architectures and evaluation practices |
| <Link url="https://doi.org/10.1071/wf22128" title="Performance of operational fire spread models in California"/> | Real-world evaluation of automated Wildfire Analyst forecasts |
| <Link url="https://doi.org/10.1071/wf25104" title="Mell et al. (2026), FDS and QUIC-Fire simulations of Camp Swift"/> | Experimental validation of detailed fire models |
| <Link url="https://elmfire.io/validation.html" title="ELMFIRE validation framework"/> | Repeatable model-validation methodology |
| <Link url="https://elmfire.io/user_guide.html" title="ELMFIRE computational and suppression guide"/> | Detailed input specifications and suppression capabilities |
| <Link url="https://agupubs.onlinelibrary.wiley.com/doi/10.1029/2024JD042561" title="Clough et al. (2025), Integrating fire observations into a coupled model"/> | Observation assimilation into WRF-SFIRE |
| <Link url="https://doi.org/10.3390/fire8100392" title="Cardoso et al. (2025), Review of wildfire spotting models"/> | Model limitations regarding firebrands and new ignitions |

## Final assessment

For a project intending to **represent active wildfires, forecast their progression, integrate a usable map interface, and ultimately test possible firefighting interventions**, ELMFIRE is the strongest first open-source system to investigate. It offers a broader set of relevant capabilities than the original Cell2Fire, particularly around spotting, probabilistic runs, and suppression.

Cell2Fire remains attractive for algorithm research and large-scale simulation experiments. C2F-W is especially relevant if the goal is to reuse an existing QGIS interface. Technosylva provides a stronger example of operational UI integration, while WRF-Fire and QUIC-Fire merit attention when detailed physical processes are central to the research question.

The greatest prospective gains may come not from choosing a more computationally sophisticated propagation algorithm, but from **combining a fast, validated simulation engine with frequently updated observations, calibrated uncertainty, and a carefully evaluated suppression model**.

The remaining limitation of this report is that it is a source-based comparative assessment, not an independent head-to-head benchmark. Establishing an empirical winner would require running each candidate on the same held-out fires, with equivalent input data, forecast lead times, and evaluation metrics.