That changes my recommendation somewhat. **VIIRS is better suited than MODIS for tracking active wildfire spread**, but there's also an important distinction between building a wildfire predictor and satisfying NASA Space Apps' actual challenge requirements.

I found the 2026 challenge **<Link url="https://www.spaceappschallenge.org/2026/find-a-team/protectech/" title="Harmonization of MODIS and VIIRS Hot Spots"/>**. Its stated objective is to create a web application that harmonizes MODIS and VIIRS fire detections into a historical burning-activity calendar, supporting the identification of unusual fire patterns and high-risk periods. <Cite ref="turn853654search2"/>

That is different from forecasting the perimeter of an active wildfire. You can certainly add a forecasting system, but I would make it an extension of the required harmonization functionality rather than replacing that functionality.

## Why VIIRS helps your model

<grid columns={2} gap={3}>
  <grid-item>
    <box gap={1}>
      <AsyncImage query="NASA FIRMS MODIS active fire hotspots western United States wildfire satellite map" aspectRatio="5:4"/>
      **MODIS**
      <title size="xl" color="default" tabularNums>1,000 m</title>
      <text size="xs" color="secondary">Nominal active-fire pixel size</text>
    </box>
  </grid-item>
  <grid-item>
    <box gap={1}>
      <AsyncImage query="NASA FIRMS VIIRS 375m active wildfire hotspots California satellite map" aspectRatio="5:4"/>
      **VIIRS**
      <title size="xl" color="default" tabularNums>375 m</title>
      <text size="xs" color="secondary">Nominal active-fire pixel size</text>
    </box>
  </grid-item>
</grid>

VIIRS provides approximately 7.1 times finer nominal pixel *area* than MODIS's 1 km product. That allows better detection of smaller fires and better localization of fire activity. NASA explicitly identifies improved mapping of large wildfire perimeters as an advantage of VIIRS. <Cite ref="turn853654search13"/>

However, neither sensor directly measures an exact continuous fire perimeter. Each hotspot is a detection associated with a satellite pixel.

The bigger benefit is the combined historical record:

| Satellite dataset | Historical coverage |
|---|---|
| MODIS Terra | November 2000 onward |
| MODIS Aqua | July 2002 onward |
| VIIRS Suomi NPP | January 2012 onward |
| VIIRS NOAA-20 | April 2018 onward |
| VIIRS NOAA-21 | January 2024 onward |

These are NASA FIRMS coverage dates. <Cite ref="turn853654search9"/>

For machine learning, this gives you over a decade of overlapping MODIS and VIIRS observations to help learn sensor differences, plus more than two decades of MODIS data for studying long-term patterns.

## How I'd structure the Space Apps project

Assuming you're participating in the 2026 <Link url="https://www.spaceappschallenge.org/2026/challenges/harmonization-of-modis-and-viirs-hot-spots/" title="official MODIS/VIIRS challenge"/>, I'd build two connected capabilities:

| Component | Purpose | Technology |
|---|---|---|
| Harmonized fire history | Fulfill the central challenge requirements | MODIS + VIIRS, spatial/temporal normalization |
| Fire activity calendar | Identify seasonal, geographical, and unusual burning patterns | Historical hotspot statistics |
| Active wildfire forecasts | Predict future fire growth from observed activity | ELMFIRE + wind + fuels + VIIRS |
| Simulation comparison | Compare forecast and subsequent observations | Historical replay and validation |
| Optional suppression experiments | Model treatment effects | Modified fuels/moisture and ELMFIRE |

For the first component, you cannot simply combine MODIS and VIIRS hotspot counts. The different detection resolutions and sensitivities can produce artificial changes in burning trends when VIIRS observations become available.

I'd build an explicitly sensor-aware harmonization method using overlapping historical observations, coverage information, and common spatial/temporal aggregation.

For the prediction component, I would start with ELMFIRE rather than WRF-SFIRE. You could then use your supercomputing resources to evaluate and train corrections across many historical fires instead of putting all of the compute into one expensive fire–atmosphere simulation.

A particularly compelling demonstration would let a user select a historical fire and a timestamp, run a forecast using only information available at that time, and then reveal how closely the prediction matches later VIIRS observations and independently mapped perimeters. That gives the judges a measurable result rather than just a simulated animation.

**One question that changes the technical direction:** Is your main objective to predict **where an already-burning wildfire will spread over the next several hours**, or to predict **where new fires are likely to ignite over the coming days or weeks**?

Those require substantially different models and training labels, even though both can use MODIS and VIIRS.