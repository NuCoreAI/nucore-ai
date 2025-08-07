import xml.etree.ElementTree as ET


class NuCorePrograms(dict):
    def __init__(self):
        super().__init__(self)
        self.program_id=1

    def __get_iox_filename(self, orig:str, index:int):
        if not orig:
            return None
        start_pos = orig.find('/')
        if start_pos == -1:
            return None  # Start delimiter not found, return original string
    
        # Find the end delimiter after the start delimiter
        end_pos = orig.find('.', start_pos + 1)
        if end_pos == -1:
            return None  # End delimiter not found, return original string
    
        # Construct the new string with the replacement
        prefix = orig[:start_pos + 1]  # Include the start delimiter
        suffix = orig[end_pos:]        # Include the end delimiter

        hex_index = format(index, 'x')
        # Pad with leading zeros to make it 4 digits
        padded_hex_index = hex_index.zfill(4)
    
        # Ensure we only return 4 digits for large numbers
        if len(padded_hex_index) > 4:
            padded_hex_index = padded_hex_index[-4:]
        return padded_hex_index.upper() 
   #     return prefix + padded_hex_index.upper() + suffix


    def __replace_program_id(self, program:str, new_id:int):
            # Parse the XML string
        root = ET.fromstring(program)
    
        # Find the first id element
        id_element = root.find('.//id')
    
        # If id element is found, update its value
        if id_element is not None:
            id_element.text = str(new_id)
        else:
            print("Warning: No <id> tag found in the XML")
    
        # Convert the modified XML back to a string
        return ET.tostring(root, encoding='unicode')

    def add_program(self, filename:str, program:str):
        if not filename or not program:
            return None
        updated_file_name=self.__get_iox_filename(filename, self.program_id)
        updated_program=self.__replace_program_id(program, self.program_id)
        
        self[updated_file_name]=updated_program 
        self.program_id+=1
        return updated_file_name, updated_program

    
if __name__ == "__main__":
    program1= """<d2d> <trigger> <id>2</id> <name>Used to be 2:Moderate Price Hue Light Optimization</name> <parent>0</parent> <if> <and/> <status OP="GE" NODE="n003_oadr3ven" ID="ST"> <val UOM="103" PREC="2">5000</val> </status> <status OP="LT" NODE="n003_oadr3ven" ID="ST"> <val UOM="103" PREC="2">7900</val> </status> </if> <then> <cmd NODE="ZB45186_011_1" ID="DON"> <p ID=""> <val UOM="51" PREC="0">90</val> </p> </cmd> </then> <else/> <comment>Set Hue lights to 90% brightness when price is between $0.50 and $0.79</comment> </trigger></d2d>"""
    program2= """<d2d> <trigger> <id>1</id> <name>Used to be 1:Normal Price Hue Light Optimization</name> <parent>0</parent> <if> <and/> <status OP="GE" NODE="n003_oadr3ven" ID="ST"> <val UOM="103" PREC="2">3000</val> </status> <status OP="LT" NODE="n003_oadr3ven" ID="ST"> <val UOM="103" PREC="2">4900</val> </status> </if> <then> <cmd NODE="ZB45186_011_1" ID="DON"> <p ID=""> <val UOM="51" PREC="0">100</val> </p> </cmd> </then> <else/> <comment>Set Hue lights to 100% brightness when price is between $0.30 and $0.49</comment> </trigger></d2d>"""
    ep=nucorePrograms()
    ep.add_program("D2D/0002.PGM", program1)
    ep.add_program("D2D/0001.PGM", program2)

    from .nucore_backend_api import nucoreAPI
    eap = nucoreAPI()
    eap.upload_programs(ep)
    print ('here')