# This file contains the condition classes for the NuCore scheduling system.
# Conditions are either Change of State or Change of Control (something is physically changed)
# Schedules are also conditions but they are in their own category and class.

from abc import ABC, abstractmethod
from typing import Literal
import xml.etree.ElementTree as ET

NUCORE_OP_CODES = Literal["GE", "LE", "EQ", "GT", "LT", "IS", "ISNOT"]

class Condition(ABC):
    def __init__(self, node: str, id:str, op:NUCORE_OP_CODES):
        '''
            :param node: The address of the node for which the property changed
            :param id: The _property id_ for the change of state OR the _command id_ for Change of Control 
            :param op: The operator for the condition
        '''
        self.node = node
        self.id = id
        self.op = op

    @abstractmethod
    def get_condition(self):
        '''
        :return: The condition in xml 
        '''
        pass

    @abstractmethod
    def parse_condition(self, condition:str):
        '''
        :param condition: The condition in xml
        '''
        pass


class COSConditions(Condition):
    '''
        COS Condition is a change of state condition. i.e. the property of a device changes its state.
        Example of Change of State:
        <status id="ST" node="n003_oadr3ven" op="GE">
            <val uom="103" prec="4">8000</val>
        </status>
        op is the operator for the condition [ GE, LE, EQ, GT, LT, IS, ISNOT ]
        id is the property id for the change of state
        node is the address of the node for which the property changed
    '''
    def __init__(self, node: str=None, id:str=None, op:NUCORE_OP_CODES=None, val:int=None, uom:str=None, prec:int=None):
        super().__init__(node, id, op)
        self.val = val
        self.uom = uom
        self.prec = prec

    def get_condition(self):
        '''
        :return: The condition in xml
        '''
        return f'''
        <status id="{self.id}" node="{self.node}" op="{self.op}">
            <val uom="{self.uom}" prec="{self.prec}">{self.val}</val>
        </status>
        '''
    def parse_condition(self, condition:str):
        '''
        :param condition: The condition in xml
        '''
        try:
            root = ET.fromstring(condition)
            self.id = root.attrib['id']
            self.node = root.attrib['node']
            self.op = root.attrib['op']
            self.val = int(root.find('val').text)
            self.uom = root.find('val').attrib['uom']
            self.prec = int(root.find('val').attrib['prec'])
        except ET.ParseError:
            pass


class COCConditions(Condition):
    '''
        COC Condition is a physical change done to the device. For instance, someone physically turn on/off the switch.
        If the switch was already on, there's no change of state.

        Example of Change of Control:
        <control id="DON" node="ZY003_1" op="IS"></control>
        op is the operator for the condition [ GE, LE, EQ, GT, LT, IS, ISNOT ]
        id is the command id for the change of control
        node is the address of the node on which there was physical activity 
    '''
    def __init__(self, node: str=None, id:str=None, op:NUCORE_OP_CODES=None):
        super().__init__(node, id, op)

    def get_condition(self):
        '''
        :return: The condition in xml
        '''
        return f'''
        <control id="{self.id}" node="{self.node}" op="{self.op}"></control>
        '''

    def parse_condition(self, condition:str):
        '''
        :param condition: The condition in xml
        '''
        try:
            root = ET.fromstring(condition)
            self.id = root.attrib['id']
            self.node = root.attrib['node']
            self.op = root.attrib['op']
        except ET.ParseError:
            pass