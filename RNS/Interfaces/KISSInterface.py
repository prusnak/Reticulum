from .Interface import Interface
from time import sleep
import sys
import serial
import threading
import time
import RNS

class KISS():
    FEND              = 0xC0
    FESC              = 0xDB
    TFEND             = 0xDC
    TFESC             = 0xDD
    CMD_UNKNOWN       = 0xFE
    CMD_DATA          = 0x00
    CMD_TXDELAY       = 0x01
    CMD_P             = 0x02
    CMD_SLOTTIME      = 0x03
    CMD_TXTAIL        = 0x04
    CMD_FULLDUPLEX    = 0x05
    CMD_SETHARDWARE   = 0x06
    CMD_READY         = 0x0F
    CMD_RETURN        = 0xFF

    @staticmethod
    def escape(data):
        data = data.replace(bytes([0xdb]), bytes([0xdb, 0xdd]))
        data = data.replace(bytes([0xc0]), bytes([0xdb, 0xdc]))
        return data

class KISSInterface(Interface):
    MAX_CHUNK = 32768

    owner    = None
    port     = None
    speed    = None
    databits = None
    parity   = None
    stopbits = None
    serial   = None

    def __init__(self, owner, name, port, speed, databits, parity, stopbits, preamble, txtail, persistence, slottime, flow_control, beacon_interval, beacon_data):
        if beacon_data == None:
            beacon_data = ""

        self.serial   = None
        self.owner    = owner
        self.name     = name
        self.port     = port
        self.speed    = speed
        self.databits = databits
        self.parity   = serial.PARITY_NONE
        self.stopbits = stopbits
        self.timeout  = 100
        self.online   = False
        self.beacon_i = beacon_interval
        self.beacon_d = beacon_data.encode("utf-8")
        self.first_tx = None

        self.packet_queue    = []
        self.flow_control    = flow_control
        self.interface_ready = False
        self.flow_control_timeout = 5
        self.flow_control_locked  = time.time()

        self.preamble    = preamble if preamble != None else 350;
        self.txtail      = txtail if txtail != None else 20;
        self.persistence = persistence if persistence != None else 64;
        self.slottime    = slottime if slottime != None else 20;

        if parity.lower() == "e" or parity.lower() == "even":
            self.parity = serial.PARITY_EVEN

        if parity.lower() == "o" or parity.lower() == "odd":
            self.parity = serial.PARITY_ODD

        try:
            RNS.log("Opening serial port "+self.port+"...")
            self.serial = serial.Serial(
                port = self.port,
                baudrate = self.speed,
                bytesize = self.databits,
                parity = self.parity,
                stopbits = self.stopbits,
                xonxoff = False,
                rtscts = False,
                timeout = 0,
                inter_byte_timeout = None,
                write_timeout = None,
                dsrdtr = False,
            )
        except Exception as e:
            RNS.log("Could not open serial port "+self.port, RNS.LOG_ERROR)
            raise e

        if self.serial.is_open:
            # Allow time for interface to initialise before config
            sleep(2.0)
            thread = threading.Thread(target=self.readLoop)
            thread.setDaemon(True)
            thread.start()
            self.online = True
            RNS.log("Serial port "+self.port+" is now open")
            RNS.log("Configuring KISS interface parameters...")
            self.setPreamble(self.preamble)
            self.setTxTail(self.txtail)
            self.setPersistence(self.persistence)
            self.setSlotTime(self.slottime)
            self.setFlowControl(self.flow_control)
            self.interface_ready = True
            RNS.log("KISS interface configured")
        else:
            raise IOError("Could not open serial port")


    def setPreamble(self, preamble):
        preamble_ms = preamble
        preamble = int(preamble_ms / 10)
        if preamble < 0:
            preamble = 0
        if preamble > 255:
            preamble = 255

        kiss_command = bytes([KISS.FEND])+bytes([KISS.CMD_TXDELAY])+bytes([preamble])+bytes([KISS.FEND])
        written = self.serial.write(kiss_command)
        if written != len(kiss_command):
            raise IOError("Could not configure KISS interface preamble to "+str(preamble_ms)+" (command value "+str(preamble)+")")

    def setTxTail(self, txtail):
        txtail_ms = txtail
        txtail = int(txtail_ms / 10)
        if txtail < 0:
            txtail = 0
        if txtail > 255:
            txtail = 255

        kiss_command = bytes([KISS.FEND])+bytes([KISS.CMD_TXTAIL])+bytes([txtail])+bytes([KISS.FEND])
        written = self.serial.write(kiss_command)
        if written != len(kiss_command):
            raise IOError("Could not configure KISS interface TX tail to "+str(txtail_ms)+" (command value "+str(txtail)+")")

    def setPersistence(self, persistence):
        if persistence < 0:
            persistence = 0
        if persistence > 255:
            persistence = 255

        kiss_command = bytes([KISS.FEND])+bytes([KISS.CMD_P])+bytes([persistence])+bytes([KISS.FEND])
        written = self.serial.write(kiss_command)
        if written != len(kiss_command):
            raise IOError("Could not configure KISS interface persistence to "+str(persistence))

    def setSlotTime(self, slottime):
        slottime_ms = slottime
        slottime = int(slottime_ms / 10)
        if slottime < 0:
            slottime = 0
        if slottime > 255:
            slottime = 255

        kiss_command = bytes([KISS.FEND])+bytes([KISS.CMD_SLOTTIME])+bytes([slottime])+bytes([KISS.FEND])
        written = self.serial.write(kiss_command)
        if written != len(kiss_command):
            raise IOError("Could not configure KISS interface slot time to "+str(slottime_ms)+" (command value "+str(slottime)+")")

    def setFlowControl(self, flow_control):
        kiss_command = bytes([KISS.FEND])+bytes([KISS.CMD_READY])+bytes([0x01])+bytes([KISS.FEND])
        written = self.serial.write(kiss_command)
        if written != len(kiss_command):
            if (flow_control):
                raise IOError("Could not enable KISS interface flow control")
            else:
                raise IOError("Could not enable KISS interface flow control")


    def processIncoming(self, data):
        self.owner.inbound(data, self)


    def processOutgoing(self,data):
        if self.online:
            if self.interface_ready:
                if self.flow_control:
                    self.interface_ready = False
                    self.flow_control_locked = time.time()

                data = data.replace(bytes([0xdb]), bytes([0xdb])+bytes([0xdd]))
                data = data.replace(bytes([0xc0]), bytes([0xdb])+bytes([0xdc]))
                frame = bytes([KISS.FEND])+bytes([0x00])+data+bytes([KISS.FEND])

                written = self.serial.write(frame)

                if data == self.beacon_d:
                    self.first_tx = None
                else:
                    if self.first_tx == None:
                        self.first_tx = time.time()

                if written != len(frame):
                    raise IOError("Serial interface only wrote "+str(written)+" bytes of "+str(len(data)))

            else:
                self.queue(data)

    def queue(self, data):
        self.packet_queue.append(data)

    def process_queue(self):
        if len(self.packet_queue) > 0:
            data = self.packet_queue.pop(0)
            self.interface_ready = True
            self.processOutgoing(data)
        elif len(self.packet_queue) == 0:
            self.interface_ready = True

    def readLoop(self):
        try:
            in_frame = False
            escape = False
            command = KISS.CMD_UNKNOWN
            data_buffer = b""
            last_read_ms = int(time.time()*1000)

            while self.serial.is_open:
                if self.serial.in_waiting:
                    byte = ord(self.serial.read(1))
                    last_read_ms = int(time.time()*1000)

                    if (in_frame and byte == KISS.FEND and command == KISS.CMD_DATA):
                        in_frame = False
                        self.processIncoming(data_buffer)
                    elif (byte == KISS.FEND):
                        in_frame = True
                        command = KISS.CMD_UNKNOWN
                        data_buffer = b""
                    elif (in_frame and len(data_buffer) < RNS.Reticulum.MTU):
                        if (len(data_buffer) == 0 and command == KISS.CMD_UNKNOWN):
                            # We only support one HDLC port for now, so
                            # strip off the port nibble
                            byte = byte & 0x0F
                            command = byte
                        elif (command == KISS.CMD_DATA):
                            if (byte == KISS.FESC):
                                escape = True
                            else:
                                if (escape):
                                    if (byte == KISS.TFEND):
                                        byte = KISS.FEND
                                    if (byte == KISS.TFESC):
                                        byte = KISS.FESC
                                    escape = False
                                data_buffer = data_buffer+bytes([byte])
                        elif (command == KISS.CMD_READY):
                            self.process_queue()
                else:
                    time_since_last = int(time.time()*1000) - last_read_ms
                    if len(data_buffer) > 0 and time_since_last > self.timeout:
                        data_buffer = b""
                        in_frame = False
                        command = KISS.CMD_UNKNOWN
                        escape = False
                    sleep(0.05)

                    if self.flow_control:
                        if not self.interface_ready:
                            if time.time() > self.flow_control_locked + self.flow_control_timeout:
                                RNS.log("Interface "+str(self)+" is unlocking flow control due to time-out. This should not happen. Your hardware might have missed a flow-control READY command, or maybe it does not support flow-control.", RNS.LOG_WARNING)
                                self.process_queue()

                    if self.beacon_i != None and self.beacon_d != None:
                        if self.first_tx != None:
                            if time.time() > self.first_tx + self.beacon_i:
                                RNS.log("Interface "+str(self)+" is transmitting beacon data: "+str(self.beacon_d.decode("utf-8")), RNS.LOG_DEBUG)
                                self.first_tx = None
                                self.processOutgoing(self.beacon_d)

        except Exception as e:
            self.online = False
            RNS.log("A serial port error occurred, the contained exception was: "+str(e), RNS.LOG_ERROR)
            RNS.log("The interface "+str(self.name)+" is now offline. Restart Reticulum to attempt reconnection.", RNS.LOG_ERROR)

    def __str__(self):
        return "KISSInterface["+self.name+"]"