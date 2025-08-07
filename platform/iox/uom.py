from dataclasses import dataclass


@dataclass(frozen=True)  # Making it frozen as these are predefined constants
class UOMEntry:
    """
    Represents a predefined Unit of Measure (UOM) entry.
    """

    id: str
    description: str
    label: str
    name: str
    category_id: str = None

    def __str__(self):
        return f"{self.name} ({self.label})"

UNKNOWN_UOM = 0

PREDEFINED_UOMS = {
    "0": UOMEntry(
        id=f"{UNKNOWN_UOM}",
        description="The unit of measure is unknown",
        label="Unknown",
        name="",
    ),
    "1": UOMEntry(
        id="1",
        description="Electrical current in Amperes",
        label="Amps",
        name="Amps",
        category_id="electric_current",
    ),
    "2": UOMEntry(
        id="2",
        description="A boolean value where 0 = False, 1 = True",
        label="Boolean",
        name="",
    ),
    "3": UOMEntry(
        id="3",
        description="BTU/Hour",
        label="btu/h",
        name="btu/h",
        category_id="power",
    ),
    "4": UOMEntry(
        id="4",
        description="Degree of temperature in Celsius",
        label="Celsius",
        name="\u00B0C",
        category_id="temperature",
    ),
    "5": UOMEntry(
        id="5",
        description="Centimeters",
        label="Centimeters",
        name="cm",
        category_id="distance",
    ),
    "6": UOMEntry(
        id="6",
        description="Cubic Feet",
        label="Cubic Feet",
        name="ft\u00B3",
        category_id="volume",
    ),
    "7": UOMEntry(
        id="7",
        description="Cubic Feet/Minute",
        label="cfm",
        name="cfm",
        category_id="volume_flow",
    ),
    "8": UOMEntry(
        id="8",
        description="Cubic Meter",
        label="Cubic Meter",
        name="m\u00B3",
        category_id="volume",
    ),
    "9": UOMEntry(
        id="9", description="Day of the Month", label="day", name=""
    ),
    "10": UOMEntry(
        id="10",
        description="Duration in Days",
        label="Days",
        name="days",
        category_id="time_duration",
    ),
    "11": UOMEntry(
        id="11",
        description="The position of the Deadbolt",
        label="Deadbolt Position",
        name="",
    ),
    "12": UOMEntry(
        id="12",
        description="The number of Decibels",
        label="Decibel",
        name="dB",
    ),
    "13": UOMEntry(
        id="13",
        description="The number of A-weighted Decibels",
        label="dBA",
        name="dBA",
    ),
    "14": UOMEntry(
        id="14",
        description="Generic Degree of temperature",
        label="Degrees",
        name="\u00B0",
        category_id="temperature",
    ),
    "15": UOMEntry(
        id="15",
        description="Door Lock alarm type",
        label="Door Lock Alarm",
        name="",
    ),
    "16": UOMEntry(
        id="16",
        description="European macroseismic",
        label="European macroseismic",
        name="European macroseismic",
    ),
    "17": UOMEntry(
        id="17",
        description="Degree of temperature in Fahrenheit",
        label="Fahrenheit",
        name="\u00B0F",
        category_id="temperature",
    ),
    "18": UOMEntry(
        id="18",
        description="Feet",
        label="Feet",
        name="feet",
        category_id="distance",
    ),
    "19": UOMEntry(
        id="19", description="Hour on the clock", label="Hour", name="Hour"
    ),
    "20": UOMEntry(
        id="20",
        description="A duration in Hours",
        label="Hours",
        name="Hours",
        category_id="time_duration",
    ),
    "21": UOMEntry(
        id="21",
        description="The Absolute Humidity",
        label="Absolute Humidity",
        name="Absolute Humidity",
    ),
    "22": UOMEntry(
        id="22",
        description="The Relative Humidity",
        label="Relative Humidity",
        name="%",
    ),
    "23": UOMEntry(
        id="23",
        description="Inches of Mercury (inHg)",
        label="inches of Hg",
        name="inHg",
        category_id="pressure",
    ),
    "24": UOMEntry(
        id="24",
        description="Inches per hour",
        label="inches/hour",
        name="inches/hour",
        category_id="speed",
    ),
    "25": UOMEntry(
        id="25",
        description="The list index of a value for a given list of values.",
        label="Index",
        name="",
    ),
    "26": UOMEntry(
        id="26",
        description="Degree of temperature in Kelvin",
        label="Kelvin",
        name="K",
        category_id="temperature",
    ),
    "27": UOMEntry(
        id="27", description="Keyword", label="Keyword", name=""
    ),  # Was missing from my manual short list, added back
    "28": UOMEntry(
        id="28",
        description="Weight in Kilograms",
        label="Kilograms",
        name="kg",
        category_id="weight",
    ),
    "29": UOMEntry(
        id="29",
        description="Kilovolts",
        label="Kilovolts",
        name="kV",
        category_id="voltage",
    ),
    "30": UOMEntry(
        id="30",
        description="Kilowatts",
        label="Kilowatts",
        name="kW",
        category_id="power",
    ),
    "31": UOMEntry(
        id="31",
        description="Kilopascals",
        label="Kilopascals",
        name="kPa",
        category_id="pressure",
    ),
    "32": UOMEntry(
        id="32",
        description="Kilometers/Hour",
        label="km/h",
        name="km/h",
        category_id="speed",
    ),
    "33": UOMEntry(
        id="33",
        description="Kilowatt hour",
        label="kWh",
        name="kWh",
        category_id="energy",
    ),
    "34": UOMEntry(
        id="34",
        description="Liedu seismic intensity scale",
        label="Liedu",
        name="Liedu",
    ),
    "35": UOMEntry(
        id="35",
        description="Liter",
        label="liters",
        name="liters",
        category_id="volume",
    ),
    "36": UOMEntry(
        id="36", description="Measure of light in lux", label="Lux", name="lux"
    ),
    "37": UOMEntry(
        id="37",
        description="Mercalli seismic intensity scale",
        label="Mercalli",
        name="Mercalli",
    ),
    "38": UOMEntry(
        id="38",
        description="Meter",
        label="Meters",
        name="meters",
        category_id="distance",
    ),
    "39": UOMEntry(
        id="39",
        description="Number of Cubic Meters per Hour",
        label="Cubic Meters/Hour",
        name="m\u00B3/h",
        category_id="volume_flow",
    ),
    "40": UOMEntry(
        id="40",
        description="Number of meters per second",
        label="Meters/Second",
        name="meters/sec",
        category_id="speed",
    ),
    "41": UOMEntry(
        id="41",
        description="Milliamp",
        label="Milliamp",
        name="mA",
        category_id="electric_current",
    ),
    "42": UOMEntry(
        id="42",
        description="Millisecond on the clock",
        label="millisecond",
        name="ms",
        category_id="time_duration",
    ),
    "43": UOMEntry(
        id="43",
        description="Millivolt",
        label="Millivolt",
        name="mV",
        category_id="voltage",
    ),
    "44": UOMEntry(
        id="44",
        description="Minute on the clock",
        label="Minute",
        name="minute",
        category_id="time_duration",
    ),
    "45": UOMEntry(
        id="45",
        description="Duration in minutes",
        label="Minutes",
        name="minutes",
        category_id="time_duration",
    ),
    "46": UOMEntry(
        id="46",
        description="Millimeters/hour",
        label="mm/hr",
        name="mm/hr",
        category_id="speed",
    ),
    "47": UOMEntry(id="47", description="Month", label="Month", name=""),
    "48": UOMEntry(
        id="48",
        description="Miles/Hour",
        label="mph",
        name="mph",
        category_id="speed",
    ),
    "49": UOMEntry(
        id="49",
        description="Meters per second",
        label="meters/sec",
        name="meters/sec",
        category_id="speed",
    ),  # Note: ID 40 and 49 are similar but distinct in source.
    "50": UOMEntry(
        id="50",
        description="Electrical resistence in Ohms",
        label="Ohms",
        name="\u2126",
        category_id="resistance",
    ),
    "51": UOMEntry(
        id="51", 
        description="Percent", 
        label="%", 
        name="%"
    ),
    "52": UOMEntry(
        id="52",
        description="Weight in Pounds",
        label="lb",
        name="lb",
        category_id="weight",
    ),
    "53": UOMEntry(
        id="53", description="Power factor", label="Power factor", name=""
    ),
    "54": UOMEntry(
        id="54", description="Parts Per Million", label="PPM", name="PPM"
    ),
    "55": UOMEntry(
        id="55", description="Pulse Count", label="Pulse Count", name=""
    ),
    "56": UOMEntry(
        id="56",
        description="The raw value used by the device",
        label="Raw",
        name="",
    ),
    "57": UOMEntry(
        id="57",
        description="Second on a clock",
        label="second",
        name="second",
        category_id="time_duration",
    ),
    "58": UOMEntry(
        id="58",
        description="Duration in seconds",
        label="seconds",
        name="seconds",
        category_id="time_duration",
    ),
    "59": UOMEntry(
        id="59",
        description="Siemens per meter",
        label="Siemens/meter",
        name="Siemens/meter",
    ),
    "60": UOMEntry(
        id="60",
        description="Body Wave Magnitude Scale",
        label="Body Wave",
        name="M_b",
    ),
    "61": UOMEntry(
        id="61",
        description="Seismic activity level using the Richter Scale",
        label="Richter Scale",
        name="M_L",
    ),
    "62": UOMEntry(
        id="62",
        description="Moment Magnitude Scale",
        label="Moment Magnitude Scale",
        name="M_w",
    ),
    "63": UOMEntry(
        id="63",
        description="Surface Wave Magnitude Scale",
        label="Surface Wave",
        name="M_s",
    ),
    "64": UOMEntry(
        id="64",
        description="Shindo seismic activity scale",
        label="Shindo",
        name="Shindo",
    ),
    "65": UOMEntry(
        id="65",
        description="Reserved for future use",
        label="Reserved for future use",
        name="Reserved for future use",
    ),
    "66": UOMEntry(
        id="66",
        description="Heating/Cooling state of the thermostat",
        label="Heat/Cool State",
        name="",
    ),
    "67": UOMEntry(
        id="67",
        description="Thermostat operational mode",
        label="Thermostat Mode",
        name="",
    ),
    "68": UOMEntry(
        id="68", description="Thermostat fan mode", label="", name=""
    ),
    "69": UOMEntry(
        id="69",
        description="US Gallons",
        label="US gallons",
        name="US gallons",
        category_id="volume",
    ),
    "70": UOMEntry(
        id="70",
        description="A number identifying a user",
        label="User Number",
        name="",
    ),
    "71": UOMEntry(
        id="71",
        description="Ultraviolet Index",
        label="UV Index",
        name="UV Index",
    ),
    "72": UOMEntry(
        id="72",
        description="Volts",
        label="Volts",
        name="Volts",
        category_id="voltage",
    ),
    "73": UOMEntry(
        id="73",
        description="Power in Watts",
        label="Watts",
        name="Watts",
        category_id="power",
    ),
    "74": UOMEntry(
        id="74",
        description="Watts per square meter",
        label="Watts/m\u00B2",
        name="Watts/m\u00B2",
    ),
    "75": UOMEntry(id="75", description="Weekday", label="Weekday", name=""),
    "76": UOMEntry(
        id="76",
        description="A 1-360 degree clockwise Wind Direction, 0 indicates no wind",
        label="Wind Direction Degrees",
        name="\u00B0",
        category_id="direction",
    ),
    "77": UOMEntry(id="77", description="Year", label="Year", name=""),
    "78": UOMEntry(
        id="78",
        description="On or off, where Off=0, On=100, Unknown=101",
        label="Off/On",
        name="",
    ),
    "79": UOMEntry(
        id="79",
        description="Open or Closed, where Open=0, Closed=100, Unknown=101",
        label="Open/Closed",
        name="",
    ),
    "80": UOMEntry(
        id="80",
        description="The running state of the Fan",
        label="Fan State",
        name="",
    ),
    "81": UOMEntry(
        id="81",
        description="Fan Mode Override",
        label="Fan Mode Override",
        name="",
    ),
    "82": UOMEntry(
        id="82",
        description="Millimeter",
        label="Millimeter",
        name="mm",
        category_id="distance",
    ),
    "83": UOMEntry(
        id="83",
        description="Kilometer",
        label="Kilometer",
        name="km",
        category_id="distance",
    ),
    "84": UOMEntry(
        id="84", description="Secure Mode", label="Secure Mode", name=""
    ),
    "85": UOMEntry(
        id="85",
        description="Resistivity in ohm-meters",
        label="\u2126m",
        name="\u2126m",
    ),
    "86": UOMEntry(
        id="86",
        description="KiloOhm",
        label="K\u2126",
        name="K\u2126",
        category_id="resistance",
    ),
    "87": UOMEntry(
        id="87",
        description="Cubic Meter/Cubic Meter",
        label="m\u00B3/m\u00B3",
        name="m\u00B3/m\u00B3",
    ),
    "88": UOMEntry(
        id="88",
        description="Water Activity",
        label="Water Activity",
        name="aw",
    ),
    "89": UOMEntry(
        id="89", description="Rotations/Minute (RPM)", label="RPM", name="RPM"
    ),
    "90": UOMEntry(
        id="90",
        description="Frequency in Hertz (1 hertz = one cycle per second)",
        label="Hz",
        name="Hz",
        category_id="frequency",
    ),
    "91": UOMEntry(
        id="91",
        description="Degrees relative to north pole of standing eye view",
        label="Degrees North",
        name="Degrees North",
        category_id="direction",
    ),
    "92": UOMEntry(
        id="92",
        description="Degrees relative to south pole of standing eye view",
        label="Degrees South",
        name="Degrees South",
        category_id="direction",
    ),
    "93": UOMEntry(
        id="93",
        description="Power Management Alarm",
        label="Power Management Alarm",
        name="",
    ),
    "94": UOMEntry(
        id="94",
        description="Appliance Alarm",
        label="Appliance Alarm",
        name="",
    ),
    "95": UOMEntry(
        id="95",
        description="Home Health Alarm",
        label="Home Health Alarm",
        name="",
    ),
    "96": UOMEntry(
        id="96",
        description="Volatile Organic Compound (VOC) Level",
        label="VOC Level",
        name="",
    ),
    "97": UOMEntry(
        id="97", description="Barrier Status", label="Barrier Status", name="%"
    ),
    "98": UOMEntry(
        id="98", description="Insteon Thermostat Mode", label="Mode", name=""
    ),
    "99": UOMEntry(
        id="99",
        description="Insteon Thermostat Fan Mode",
        label="Fan Mode",
        name="",
    ),
    "100": UOMEntry(
        id="100",
        description="A Level from 0-255 (for example, the brightness of a dimmable lamp)",
        label="Level",
        name="",
    ),
    "101": UOMEntry(
        id="101",
        description="Degree multiplied by 2",
        label="Degree X 2",
        name="Degree X 2",
    ),
    "102": UOMEntry(
        id="102",
        description="Kilowatt second",
        label="kWs",
        name="kWs",
        category_id="energy",
    ),
    "103": UOMEntry(
        id="103", description="Dollars", label="Dollars", name="$"
    ),
    "104": UOMEntry(
        id="104", description="Cents", label="Cents", name="Cents"
    ),
    "105": UOMEntry(
        id="105",
        description="Inches",
        label="Inches",
        name="Inches",
        category_id="distance",
    ),
    "106": UOMEntry(
        id="106",
        description="Millimeters per Day",
        label="Millimeters/Day",
        name="mm/day",
        category_id="speed",
    ),
    "107": UOMEntry(
        id="107",
        description="Raw 1-Byte unsigned value",
        label="1 Byte (Unsigned)",
        name="",
    ),
    "108": UOMEntry(
        id="108",
        description="Raw 2-Byte unsigned value",
        label="2 Bytes (Unsigned)",
        name="",
    ),
    "109": UOMEntry(
        id="109",
        description="Raw 3-Byte unsigned value",
        label="3 Bytes (Unsigned)",
        name="",
    ),
    "110": UOMEntry(
        id="110",
        description="Raw 4-Byte unsigned value",
        label="4 Bytes (Unsigned)",
        name="",
    ),
    "111": UOMEntry(
        id="111",
        description="Raw 1-Byte signed value",
        label="1 Byte",
        name="",
    ),
    "112": UOMEntry(
        id="112",
        description="Raw 2-Byte signed value",
        label="2 Byte",
        name="",
    ),
    "113": UOMEntry(
        id="113",
        description="Raw 3-Byte signed value",
        label="3 Byte",
        name="",
    ),
    "114": UOMEntry(
        id="114",
        description="Raw 4-Byte signed value",
        label="4 Byte",
        name="",
    ),
    "115": UOMEntry(
        id="115",
        description="Most recent On style action taken for lamp control",
        label="Action",
        name="",
    ),
    "116": UOMEntry(
        id="116",
        description="Miles",
        label="Miles",
        name="Miles",
        category_id="distance",
    ),
    "117": UOMEntry(
        id="117",
        description="Millibars, typically used in barometric reports",
        label="Millibars",
        name="mb",
        category_id="pressure",
    ),
    "118": UOMEntry(
        id="118",
        description="Hectopascals, typically used in barometric reports",
        label="Hectopascals",
        name="hPa",
        category_id="pressure",
    ),
    "119": UOMEntry(
        id="119",
        description="Watt Hour",
        label="Wh",
        name="Wh",
        category_id="energy",
    ),
    "120": UOMEntry(
        id="120",
        description="Inches per day",
        label="inches/day",
        name="inches/day",
        category_id="speed",
    ),
    "121": UOMEntry(
        id="121",
        description="Mole per cubic meter (mol/m3)",
        label="mol/m\u00B3",
        name="mol/m\u00B3",
    ),
    "122": UOMEntry(
        id="122",
        description="Microgram per cubic meter (\u00B5g/m\u00B3)",
        label="\u00B5g/m\u00B3",
        name="\u00B5g/m\u00B3",
    ),
    "123": UOMEntry(
        id="123",
        description="Becquerel per cubic meter (bq/m\u00B3)",
        label="Bq/m\u00B3",
        name="Bq/m\u00B3",
    ),
    "124": UOMEntry(
        id="124",
        description="Picocuries per liter (pCi/l)",
        label="pCi/l",
        name="pCi/l",
    ),
    "125": UOMEntry(
        id="125", description="Acidity (pH)", label="pH", name="pH"
    ),
    "126": UOMEntry(
        id="126", description="Beats per Minute (bpm)", label="bpm", name="bpm"
    ),
    "127": UOMEntry(
        id="127",
        description="Millimeters of mercury (mmHg)",
        label="mmHg",
        name="mmHg",
        category_id="pressure",
    ),
    "128": UOMEntry(
        id="128",
        description="Joule (J)",
        label="J",
        name="J",
        category_id="energy",
    ),
    "129": UOMEntry(
        id="129", description="Body Mass Index (BMI)", label="BMI", name="BMI"
    ),
    "130": UOMEntry(
        id="130",
        description="Liters per hour (l/h)",
        label="l/h",
        name="l/h",
        category_id="volume_flow",
    ),
    "131": UOMEntry(
        id="131",
        description="Decibel Milliwatts (dBm)",
        label="dBm",
        name="dBm",
    ),
    "132": UOMEntry(
        id="132",
        description="Breaths per minute (brpm)",
        label="brpm",
        name="brpm",
    ),
    "133": UOMEntry(
        id="133",
        description="Kilohertz (kHz)",
        label="kHz",
        name="kHz",
        category_id="frequency",
    ),
    "134": UOMEntry(
        id="134",
        description="Meters per squared Seconds (m/sec2)",
        label="m/sec\u00B2",
        name="m/sec\u00B2",
    ),
    "135": UOMEntry(
        id="135", description="Volt-Amp (VA)", label="VA", name="VA"
    ),
    "136": UOMEntry(
        id="136", description="Volt-Amp Reactive", label="VAR", name="VAR"
    ),
    "137": UOMEntry(
        id="137", description="NTP Date/Time", label="NTP", name="NTP"
    ),
    "138": UOMEntry(
        id="138",
        description="Pound per square inch (PSI)",
        label="PSI",
        name="PSI",
        category_id="pressure",
    ),
    "139": UOMEntry(
        id="139",
        description="Direction 0-360 degrees",
        label="Direction",
        name="\u00B0 Direction",
        category_id="direction",
    ),
    "140": UOMEntry(
        id="140",
        description="Milligram per liter (mg/l)",
        label="mg/l",
        name="mg/l",
    ),
    "141": UOMEntry(id="141", description="Newton", label="N", name="N"),
    "142": UOMEntry(
        id="142",
        description="US Gallons per second",
        label="gal/sec",
        name="gal/sec",
        category_id="volume_flow",
    ),
    "143": UOMEntry(
        id="143",
        description="US Gallons per minute (gpm)",
        label="gpm",
        name="gpm",
        category_id="volume_flow",
    ),
    "144": UOMEntry(
        id="144",
        description="US Gallons per hour",
        label="gal/hour",
        name="gal/hour",
        category_id="volume_flow",
    ),
    "145": UOMEntry(id="145", description="Text", label="Text", name="Text"),
    "146": UOMEntry(
        id="146",
        description="Short Notification ID",
        label="Notification ID",
        name="Notification ID",
    ),
    "147": UOMEntry(id="147", description="XML", label="XML", name="XML"),
    "148": UOMEntry(
        id="148",
        description="Full Notification ID",
        label="Notification ID",
        name="Notification ID",
    ),  # Note: label and name are same as 146, but ID and description differ.
    "149": UOMEntry(
        id="149",
        description="Hue in Degrees",
        label="Hue Degree",
        name="\u00B0",
        category_id="direction",
    ),
    "150": UOMEntry(
        id="150",
        description="URL data stream",
        label="URL Stream",
        name="URL Stream",
    ),
    "151": UOMEntry(
        id="151",
        description="Unix Timestamp (seconds since Jan 1/1970, UTC)",
        label="Unix Timestamp",
        name="Unix Timestamp",
        category_id="time_duration",
    ),
    "152": UOMEntry(
        id="152",
        description="Mired (color temperature)",
        label="Mired",
        name="Mired",
    ),
    "153": UOMEntry(
        id="153",
        description="Color XY (usually a value between 0.00000 to 1.00000)",
        label="Color",
        name="Color",
    ),
    "154": UOMEntry(
        id="154",
        description="Number of steps per second",
        label="Steps / Second",
        name="Steps / Second",
    ),
}


def get_uom_by_id(uom_id):
    return PREDEFINED_UOMS.get(uom_id)


supported_uoms = list(PREDEFINED_UOMS.values())
