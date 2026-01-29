
────────────────────────────────
# DEFINITIONS 
`NuCore` is an open source and typeless Smart Home and Demand Flexibility technology
`Typeless` means that the same patterns are used for anything and therefore service, devices, virtual things, widgets can all be described in Natural Language without ascribing types
`UOM (Unit of Measure)` defines the unit for a value such as Fahrenheit, Mile, Meter, Dollars, etc.
`Precision` determines decimal places: precision=0 (whole numbers), precision=1 (tenths), precision=2 (hundredths), etc.
`Editors` define the constraints and valid values for properties and command parameters
`Properties` define real-time values (status, temperature, brightness, etc.). The permissible values are constrained by an `editor`
`Accepts Commands` define commands that can be sent to the device such as on, off, dim, etc. 
`Sends Commands` define emitted by the device. (i.e. motion sensed, someone tapping on a keypad button, etc.)
`Parameters` are extra information provided to Accepts and Sends Commands. The values for parameters are constrained by an `editor`
`COS (Change of Property State)` is an event resulting from change in property value (OFF→ON, 72→73)
`COC (Change of Control)` is an event resulting from physical control of a device, even if state does not change (captured via Sends Commands)
`Enum` is an enumerated list of permissible values for `properties` and `parameters`
`Plugin` is akin to an app on smart phones. It **plugs into** NuCore and extends NuCore's features/functionalities. 
