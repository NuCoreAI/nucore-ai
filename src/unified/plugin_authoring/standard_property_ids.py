"""The ISY/IoX catalogue of standard NodeDef ``Property`` ids (e.g. ``ST``,
``CLITEMP``, ``CLIHUM``), plus the id format every property id -- standard or
custom -- must satisfy on the real hub.

Format confirmed from ``../iox-vscode-plugin/schemas/node.properties.schema.json``
(the schema the real hub's own authoring UI enforces for a NodeDef's
``properties[].id``): all-caps, digits, and underscore only, starting with a
letter, max length 30 (``^[A-Z][A-Z0-9_]*$``, ``maxLength: 30``). Note this is
stricter than this repo's own ``nucore/schemas/defs/id.schema.json``, which is
deliberately permissive for every *other* kind of id (NodeDef/Editor/Cmd/
LinkDef) -- Property ids are the one case iox-vscode-plugin itself
constrains, so this module is the one place in this package that encodes it.

``STANDARD_PROPERTY_IDS`` is transcribed from
``../iox-vscode-plugin/schemas/old/properties.schema.json``'s enum (each
entry there is a literal ``"<label> | <ID>"`` string) -- the live, non-"old"
schema dropped the enum in favor of the bare pattern above, but the ids
themselves are still real/recognized by the hub, and are the better choice
than inventing a custom one whenever a property's meaning matches one of
them (e.g. a thermostat's current temperature should be ``CLITEMP``, not a
freshly-invented ``TEMP`` or a generic ``GV0``).
"""

from __future__ import annotations

import re

PROPERTY_ID_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
MAX_PROPERTY_ID_LENGTH = 30

STANDARD_PROPERTY_IDS: dict[str, str] = {
    "ACCX": "Acceleration X axis",
    "ACCY": "Acceleration Y axis",
    "ACCZ": "Acceleration Z axis",
    "ADRPST": "Auto DR Processing State",
    "AIRFLOW": "Air Flow",
    "ALARM": "An alarm occurred (values are different for each device type)",
    "ANGLPOS": "Angle Position",
    "AQI": "Air Quality Index",
    "ATMPRES": "Atmospheric Pressure",
    "AWAKE": "Awake",
    "BARPRES": "Barometric Pressure",
    "BATLVL": "Battery level",
    "BEEP": "Beep",
    "BMI": "Body Mass Index",
    "BMR": "Basic Metabolic Rate",
    "BONEM": "Bone Mass",
    "BPDIA": "Blood pressure Diastolic",
    "BPSYS": "Blood pressure Systolic",
    "BRT": "Brighten",
    "BUSY": "Device is Busy",
    "CC": "Current Current",
    "CH20": "Formaldehyde CH2O level",
    "CLIEMD": "Energy Mode",
    "CLIFRS": "Fan Running State",
    "CLIFS": "Fan Setting",
    "CLIFSO": "Fan Setting Override",
    "CLIHCS": "Heat/Cool State",
    "CLIHUM": "Humidity",
    "CLIMD": "Thermostat Mode",
    "CLISMD": "Schedule Mode",
    "CLISPC": "Cool Setpoint",
    "CLISPH": "Heat Setpoint",
    "CLITEMP": "Current Temperature",
    "CO": "Carbon Monoxide Level",
    "CO2LVL": "CO2 Level",
    "CPW": "Current Power Used",
    "CTL": "Controller Action",
    "CV": "Current Voltage",
    "DELAY": "Delay",
    "DEWPT": "Dew Point",
    "DFOF": "Fast Off",
    "DFON": "Fast On",
    "DIM": "Dim",
    "DISTANC": "Distance",
    "DOF": "Off",
    "DOF3": "Off 3 Key Presses",
    "DOF4": "Off 4 Key Presses",
    "DOF5": "Off 5 Key Presses",
    "DON": "On",
    "DON3": "On 3 Key Presses",
    "DON4": "On 4 Key Presses",
    "DON5": "On 5 Key Presses",
    "DUR": "Duration",
    "ELECCON": "Electrical Conductivity",
    "ELECRES": "Electrical Resistivity",
    "ERR": "Error",
    "ETO": "Evapotranspiration",
    "FATM": "Fat Mass",
    "FDDOWN": "Fade Down",
    "FDSTOP": "Fade Stop",
    "FDUP": "Fade Up",
    "FREQ": "Frequency",
    "GPV": "General Purpose Value",
    "GUST": "Gust",
    "GVOL": "Water Volume",
    "HAIL": "Hail",
    "HEATIX": "Heat Index",
    "HR": "Heart Rate",
    "LUMIN": "Luminance",
    "METHANE": "Methane Density",
    "MODE": "Mode",
    "MOIST": "Moisture",
    "MOON": "Moon Phase",
    "MUSCLEM": "Muscle Mass",
    "OL": "On Level",
    "OZONE": "Ozone",
    "PF": "Power Factor",
    "PM10": "Particulate Matter 10",
    "PM25": "Particulate Matter 2.5",
    "POP": "Percent chance of precipitation",
    "PPW": "Polarized Power Used",
    "PRECIP": "Precipitation",
    "PULSCNT": "Pulse Count",
    "QUERY": "Query Device",
    "RADON": "Radon concentration",
    "RAINRT": "Rain Rate",
    "RELMOD": "Relative modulation level",
    "RESET": "Reset values",
    "RESPR": "Respiratory rate",
    "RFSS": "RF Signal Strength",
    "ROTATE": "Rotation",
    "RR": "Ramp Rate",
    "SECMD": "Device secure mode",
    "SEISINT": "Seismic Intensity",
    "SEISMAG": "Seismic Magnitude",
    "SMOKED": "Smoke Density",
    "SOILH": "Soil Humidity",
    "SOILR": "Soil Reactivity",
    "SOILS": "Soil Salinity",
    "SOILT": "Soil Temperature",
    "SOLRAD": "Solar Radiation",
    "SPEED": "Velocity",
    "ST": "Status",
    "SVOL": "Sound Volume",
    "TANKCAP": "Tank Capacity",
    "TBW": "Total body water",
    "TEMPEXH": "Exhaust Temperature",
    "TEMPOUT": "Outside Temperature",
    "TIDELVL": "Tide Level",
    "TIME": "Time",
    "TIMEREM": "Time remaining",
    "TPW": "Total Power Used",
    "UAC": "Valid user access code entered",
    "UOM": "Unit",
    "USRNUM": "The user access code that associated with the most recent Alarm",
    "UV": "Ultraviolet",
    "VOCLVL": "Volatile Organic Compound (VOC) level",
    "WATERF": "Water Flow",
    "WATERP": "Water Pressure",
    "WATERT": "Water Temperature",
    "WATERTB": "Boiler Water Temperature",
    "WATERTD": "Domestic Hot Water Temperature",
    "WEIGHT": "Weight",
    "WINDCH": "Wind Chill",
    "WINDDIR": "Wind Direction",
    "WVOL": "Water Volume",
}


def is_valid_property_id_format(property_id: str) -> bool:
    return (
        isinstance(property_id, str)
        and len(property_id) <= MAX_PROPERTY_ID_LENGTH
        and bool(PROPERTY_ID_PATTERN.match(property_id))
    )
