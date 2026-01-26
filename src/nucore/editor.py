from dataclasses import dataclass, field
from .uom import UOMEntry, supported_uoms

REFERENCE_DELIMITER = "REFERENCE"

@dataclass
class EditorSubsetRange:
    """
    Defines a discrete set of allowed values for an editor range,
    using spans (e.g., '0-5') and individual values (e.g., '7,9').
    """


    id: str  #editor id
    uom: UOMEntry = field(metadata={"choices": supported_uoms})
    subset: str
    names: dict = field(default_factory=dict)

    def get_json_description(self, comma:bool):
        """
        Returns a JSON description of the subset.
        """
        out = f"{{\"uom\":\"{self.uom.label if self.uom.label else ' '}\",\"uom_id\":{self.uom.id}"
        out += f",\"precision\":0"
        names = self.get_json_names()
        if names:
            out += ",\"enums\":{"
            for idx, name in enumerate(names):
                out += f"{name}"
                if idx < len(names) - 1:
                    out += ","
            out += "}"
        out += "}"
        if comma:
            out += ","
        return out

    def write_description(self, writer):
        uom_label = self.uom.label if self.uom.label else ' '
        if self.uom.id == "25":
            uom_label = f"{self.id}_{self.uom.label}"

        with writer.block():
            writer.write(f"- uom:{uom_label} uom_id={self.uom.id}")
            with writer.block():
                writer.write(f"precision:0")
                names = self.get_names()
                if names:
                    writer.write("enums:")
                    with writer.block():
                        for name in names:
                            writer.write(f"- {name}")

    def get_json_names(self):
        """
        Returns a list of names for the subset.
        """
        names = []
        if not self.names:
            return names
        # Iterate through the subset and map names
        # to their corresponding values.
        # If a range is specified, it will be expanded.
        # Otherwise, it will return the individual values.
        # Example: "0-5, 7, 9" -> ["between 0-5", "7", "9"]
        # Example: "0-5, 7, 9" -> ["between 0-5", "7 [maps to 7]", "9 [maps to 9]"]
        for s in self.subset.split(","):
            s = s.strip()
            if "-" in s:
                # It's a range 'a-b'
                #names.append(f"between {s}")
                for k, v in self.names.items():
                    if k and v:
                        names.append(f"\"{k}\": \"{v}\"")
            else:
                # Single value
                val_name = self.names.get(s)
                if val_name:
                    names.append(f"\"{s}\": \"{val_name}\"")
                else:
                    names.append(f"\"{s}\": \"{s}\"")
        return names
    def get_names(self):
        """
        Returns a list of names for the subset.
        """
        names = []
        if not self.names:
            return names
        # Iterate through the subset and map names
        # to their corresponding values.
        # If a range is specified, it will be expanded.
        # Otherwise, it will return the individual values.
        # Example: "0-5, 7, 9" -> ["between 0-5", "7", "9"]
        # Example: "0-5, 7, 9" -> ["between 0-5", "7 [maps to 7]", "9 [maps to 9]"]
        for s in self.subset.split(","):
            s = s.strip()
            if "-" in s:
                # It's a range 'a-b'
                #names.append(f"between {s}")
                for k, v in self.names.items():
                    names.append(f"{k}: {v}")
            else:
                # Single value
                val_name = self.names.get(s)
                if val_name:
                    names.append(f"{s}: {val_name}")
                else:
                    names.append(s)
        return names

@dataclass
class EditorMinMaxRange:
    """
    Defines a continuous range with min, max, precision, and step attributes.
    """
    id: str  #editor id
    uom: UOMEntry = field(metadata={"choices": supported_uoms})
    min: float
    max: float
    prec: float = None
    step: float = None
    names: dict = field(default_factory=dict)

    def get_json_description(self, comma:bool):
        """
        Returns a JSON description of the range.
        """
        out = f"{{\"uom\":\"{self.uom.label if self.uom.label else ' '}\",\"uom_id\":{self.uom.id}"
        out += f",\"min\":{self.min},\"max\":{self.max},\"precision\":{self.prec if self.prec else 0}"
        if self.step is not None:
            out += f",\"step\":{self.step}"
        names = self.get_json_names()
        if names:
            out += ",\"enums\":{"
            for idx, name in enumerate(names):
                out += f"{name}"
                if idx < len(names) - 1:
                    out += ","
            out += "}"
        out += "}"
        if comma:
            out += ","
        return out

    def write_description(self, writer):
        with writer.block():
            writer.write(f"- uom:{self.uom.label if self.uom.label else ' '} uom_id={self.uom.id}")
            with writer.block():
                if self.min is not None:
                    writer.write(f"min:{self.min}")
                if self.max is not None:
                    writer.write(f"max:{self.max}")
                writer.write(f"precision:{self.prec if self.prec else 0}")
                if self.step is not None:
                    writer.write(f"step:{self.step}")
                names = self.get_names()
                if names:
                    writer.write("enums:")
                    with writer.block():
                        for name in names:
                            writer.write(f"- {name}")

    def get_names(self):
        """
        Returns a dictionary of names for the range.
        """
        names = []
        if self.names:
            for k, v in self.names.items():
                names.append(f"{k}: {v}")
        return names

    def get_json_names(self):
        """
        Returns a dictionary of names for the range.
        """
        names = []
        if self.names:
            for k, v in self.names.items():
                if k and v:
                    names.append(f"\"{k}\": \"{v}\"")
        return names

@dataclass
class Editor:
    """
    Definition of an editor, used to render a value or allow selection.
    It defines allowed values through one or more ranges.
    """

    id: str
    is_reference: bool 
    ranges: list[EditorSubsetRange | EditorMinMaxRange]

    def get_json_descriptions(self):
        """
        Get JSON descriptions, handling references - EXPERIMENTAL
        
        :param self: Description
        :param writer: Description
        """
        out = ""  
        if self.is_reference:
            out += f",\"editors\":{{\"id\":\"{REFERENCE_DELIMITER} editor id={self.id}\"}}"
            return out
        if len(self.ranges) == 0: 
            return out
        #out += f",\"editors\":{{\"id\":\"{self.id}\",\"ranges\":["
        out += f",\"editors\":["
        for i,r in enumerate(self.ranges):
            out += r.get_json_description(i < len(self.ranges) - 1)
        out += "]"
        return out


    def write_descriptions(self, writer):
        """
        Write descriptions, handling references - EXPERIMENTAL
        
        :param self: Description
        :param writer: Description
        """
        if self.is_reference:
            with writer.block():
                writer.write(f"editors id={REFERENCE_DELIMITER} id={self.id}")
            return
        if len(self.ranges) == 0: 
            return
        with writer.block():
            writer.write(f"editors id={self.id}:")
            for r in self.ranges:
                writer.write(f"{{\"id\":\"{self.id}\",")
                r.write_description(writer)
                writer.write("},")

    def write_prompt_section(self, writer, USE_JSON_OUTPUT):
        if len(self.ranges) == 0: 
            return
        with writer.block():
            if USE_JSON_OUTPUT:
                writer.write(self.get_json_descriptions())
                return
            
            writer.write(f"\n==={REFERENCE_DELIMITER} editor id={self.id}===")
            writer.write(f"editors id={self.id}:")
            for r in self.ranges:
                writer.write(f"{{\"id\":\"{self.id}\",")
                writer.write("{")
                r.write_description(writer)
