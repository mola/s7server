#!/usr/bin/env python3
import json
import struct
import ctypes
import threading
import time
import random
import math
from ctypes import c_ubyte
from typing import Dict, Any, Optional
from snap7.server import Server
from snap7.type import SrvArea


class S7Server:
    """S7 Server that reads from UDP and repopulates S7 memory areas."""
    
    def __init__(self, tcp_port: int = 1105, rack: int = 0, slot: int = 1):
        self.tcp_port = tcp_port
        self.rack = rack
        self.slot = slot
        self.running = False
        self.server = None
        
        # Memory areas
        self.db_areas = {}  # db_number -> ctypes array
        self.mk_area = (c_ubyte * 1024)()  # Merker/Flags
        self.pe_area = (c_ubyte * 1024)()  # Inputs
        self.pa_area = (c_ubyte * 1024)()  # Outputs
        
        # Connection data
        self.connections = {}  # connection_id -> connection config
        self.tag_values = {}  # connection_id -> {tag_label: value}
        
        # Statistics
        self.packet_count = 0
        self.update_count = 0
        self.last_sequence = None

        # Simulation
        self._sim_thread = None
        self._sim_running = False
        self._sim_counter = 0
        self._sim_config = self._default_sim_config()
        self._sim_db_number = 1
        self._sim_connection_id = 0
        self._sim_data = None  # shared bytearray passed to register_area

        print(f"S7 Server initialized - Port: {tcp_port}, Rack: {rack}, Slot: {slot}, DBs: {list(self.db_areas.keys())}")

    def _initialize_memory_areas(self):
        """Initialize memory areas based on connection configurations"""
        # Calculate required DB sizes based on tags
        db_sizes = {}
        for connection_id, connection in self.connections.items():
            tags = connection.get('tags', [])
            for tag in tags:
                if not tag.get('enabled', True):
                    continue
                    
                area = tag.get('area')
                if area != 'DB':
                    continue
                    
                db_number = tag.get('db_number')
                if db_number is None:
                    continue
                    
                byte_offset = tag.get('byte_offset', 0)
                length = tag.get('length', 1)
                max_offset = byte_offset + length
                
                if db_number not in db_sizes:
                    db_sizes[db_number] = max_offset
                else:
                    db_sizes[db_number] = max(db_sizes[db_number], max_offset)
        
        # Create DB areas with calculated sizes
        for db_number, size in db_sizes.items():
            if size < 256:  # Minimum size
                size = 256
            elif size > 65536:  # Maximum reasonable size
                size = 65536
            
            # Round up to next 256 bytes for efficiency
            size = ((size + 255) // 256) * 256
            
            if db_number not in self.db_areas:
                self.db_areas[db_number] = (c_ubyte * size)()
                print(f"Created DB{db_number} with size {size} bytes")
                
                # Register immediately if server is running
                if self.server and self.running:
                    self.server.register_area(SrvArea.DB, db_number, self.db_areas[db_number])
                    print(f"Registered DB{db_number} with running S7 server")

    def start(self):
        """Start the S7 server"""
        if self.running:
            return
            
        try:
            # Create and configure server
            self.server = Server(log=False)  # Disable snap7 logging to reduce noise
            self.server.create()
            
            # Register memory areas
            for db_number in self.db_areas:
                # Use shared bytearray for simulation DBs, otherwise ctypes array
                if db_number == self._sim_db_number and self._sim_data is not None:
                    self.server.register_area(SrvArea.DB, db_number, self._sim_data)
                else:
                    self.server.register_area(SrvArea.DB, db_number, self.db_areas[db_number])
                print(f"Registered DB{db_number} (Rack={self.rack}, Slot={self.slot})")
            
            # Register standard areas
            self.server.register_area(SrvArea.MK, 0, self.mk_area)
            self.server.register_area(SrvArea.PE, 0, self.pe_area)
            self.server.register_area(SrvArea.PA, 0, self.pa_area)
            
            # Start server
            self.server.start(tcp_port=self.tcp_port)
            self.running = True
            
            print(f"S7 Server started on 0.0.0.0:{self.tcp_port}")
            print(f"  Rack: {self.rack}, Slot: {self.slot}")
            print(f"  Registered {len(self.db_areas)} DB blocks: {sorted(self.db_areas.keys())}")
            
        except Exception as e:
            print(f"Failed to start S7 server: {e}")
            self.stop()
            raise

    def _default_sim_config(self) -> Dict[str, Any]:
        """Default simulation tag configuration for DB1.
        Layout:
          Offset 0-1:   Counter (WORD)  - increments 0->65535 every 1s
          Offset 2-3:   Speed (INT)     - static 1500 RPM
          Offset 4-7:   Temperature (REAL) - static 45.5 °C
          Offset 8-11:  Pressure (REAL)   - static 101.3 bar
          Offset 12-15: Flow Rate (REAL)   - static 12.5 L/min
          Offset 16:    Running (BOOL.0)   - True
          Offset 17:    Alarm (BOOL.0)     - False
          Offset 18-19: Status (WORD)      - static 1 (OK)
          Offset 20-23: Hours (DINT)       - static 12345
          Offset 24-27: Power (REAL)       - static 7.5 kW
          Offset 28-29: Humidity (INT)     - static 65 %
        """
        return [
            {'label': 'Counter',     'byte_offset': 0,  'data_type': 'WORD',  'length': 2,  'sim': 'counter'},
            {'label': 'Speed',       'byte_offset': 2,  'data_type': 'INT',   'length': 2,  'sim': 'static', 'value': 1500},
            {'label': 'Temperature', 'byte_offset': 4,  'data_type': 'REAL',  'length': 4,  'sim': 'static', 'value': 45.5},
            {'label': 'Pressure',    'byte_offset': 8,  'data_type': 'REAL',  'length': 4,  'sim': 'static', 'value': 101.3},
            {'label': 'FlowRate',    'byte_offset': 12, 'data_type': 'REAL',  'length': 4,  'sim': 'static', 'value': 12.5},
            {'label': 'Running',     'byte_offset': 16, 'data_type': 'BOOL',  'length': 1,  'sim': 'static', 'value': True,  'bit_offset': 0},
            {'label': 'Alarm',       'byte_offset': 17, 'data_type': 'BOOL',  'length': 1,  'sim': 'static', 'value': False, 'bit_offset': 0},
            {'label': 'Status',      'byte_offset': 18, 'data_type': 'WORD',  'length': 2,  'sim': 'static', 'value': 1},
            {'label': 'Hours',       'byte_offset': 20, 'data_type': 'DINT',  'length': 4,  'sim': 'static', 'value': 12345},
            {'label': 'Power',       'byte_offset': 24, 'data_type': 'REAL',  'length': 4,  'sim': 'static', 'value': 7.5},
            {'label': 'Humidity',    'byte_offset': 28, 'data_type': 'INT',   'length': 2,  'sim': 'static', 'value': 65},
        ]

    def start_simulation(self, db_number: int = 1, interval: float = 1.0,
                         custom_config: Optional[list] = None):
        """Start background simulation that writes tags into a DB every `interval` seconds.

        Args:
            db_number: DB number to write simulation data into (default 1).
            interval: update interval in seconds (default 1.0).
            custom_config: optional list of tag dicts (same shape as _default_sim_config)
                          to override the default layout.
        """
        if self._sim_running:
            print("Simulation already running")
            return

        if custom_config is not None:
            self._sim_config = custom_config

        self._sim_db_number = db_number
        self._sim_counter = 0

        # Create a shared bytearray that both simulation and server use directly
        # register_area accepts bytearray without copying, so all reads/writes share this one object
        size = 256
        self._sim_data = bytearray(size)
        # Also store in db_areas as ctypes for backward compatibility
        self.db_areas[db_number] = (c_ubyte * size)()

        # If server is already running, register this shared bytearray
        if self.server and self.running:
            self.server.register_area(SrvArea.DB, db_number, self._sim_data)
            print(f"Registered DB{db_number} with running S7 server (shared bytearray)")

        self._sim_running = True
        self._sim_thread = threading.Thread(target=self._simulation_loop, args=(interval,), daemon=True)
        self._sim_thread.start()
        print(f"Simulation started on DB{db_number} every {interval}s")

    def stop_simulation(self):
        """Stop the simulation thread."""
        self._sim_running = False
        if self._sim_thread and self._sim_thread.is_alive():
            self._sim_thread.join(timeout=3)
        self._sim_thread = None
        print("Simulation stopped")

    def _simulation_loop(self, interval: float):
        """Background loop that updates simulation values."""
        while self._sim_running:
            try:
                self._write_simulation_values()
            except Exception as e:
                print(f"Simulation write error: {e}")
            time.sleep(interval)

    def _write_simulation_values(self):
        """Compute and write all simulation tag values into the shared bytearray."""
        data = self._sim_data
        if data is None:
            return

        # Read counter from shared memory (detects external writes)
        counter_tag = next((t for t in self._sim_config if t.get('sim') == 'counter'), None)
        if counter_tag:
            offset = counter_tag['byte_offset']
            if offset + 2 <= len(data):
                current = (data[offset] << 8) | data[offset + 1]
                if current != self._sim_counter:
                    self._sim_counter = current

        self._sim_counter = (self._sim_counter + 1) % 65536

        for tag in self._sim_config:
            sim_type = tag.get('sim', 'static')
            value = self._sim_counter if sim_type == 'counter' else tag.get('value', 0)
            self._write_tag_value_to_bytearray(
                data, tag['byte_offset'], tag.get('bit_offset'),
                tag['data_type'], tag.get('length', 1), value, tag
            )

    def get_sim_statistics(self) -> Dict[str, Any]:
        """Return current simulation state."""
        return {
            'running': self._sim_running,
            'db_number': self._sim_db_number,
            'counter': self._sim_counter,
            'tags': len(self._sim_config),
        }

    def stop(self):
        """Stop the S7 server"""
        self.stop_simulation()
        self.running = False

        if self.server:
            try:
                self.server.stop()
                self.server.destroy()
                print("S7 Server stopped")
            except Exception as e:
                print(f"Error stopping S7 server: {e}")
            finally:
                self.server = None

    def update_connection_config(self, connection_data: Dict[str, Any]):
        """Update connection configuration and initialize memory areas"""
        try:
            connection_id = connection_data['id']
            self.connections[connection_id] = connection_data
            
            # Initialize tag values for this connection
            self.tag_values[connection_id] = {}
            
            # print(f"Updated connection config for ID {connection_id}: {connection_data['name']}")
            
            # Reinitialize memory areas if server is running
            if self.server and self.running:
                self._initialize_memory_areas()
                
        except Exception as e:
            print(f"Error updating connection config: {e}")

    def process_s7_data(self, data: Dict[str, Any]):
        """Process incoming S7 data from UDP"""
        try:
            # print(f"S7 Server processing data: {data.get('type')}")
            
            if not self.running:
                print("S7 Server not running - ignoring data")
                return
            
            connection = data.get('connection', {})
            connection_id = connection.get('id')
            sequence = data.get('sequence')
            packet_data = connection.get('data', {})  # Get data from connection section
            
            #print(f"Debug - packet_data: {packet_data}")
            #print(f"Debug - packet_data type: {type(packet_data)}")
            #print(f"Debug - packet_data keys: {list(packet_data.keys()) if packet_data else 'None'}")
            
            if not connection_id:
                print("No connection ID in S7 data")
                return
            
            # print(f"Processing S7 data for connection {connection_id}, sequence {sequence}")
            
            # Check for duplicate packets
            if sequence is not None and sequence == self.last_sequence:
                print(f"Duplicate sequence {sequence} - ignoring")
                return
            self.last_sequence = sequence
            
            # Update connection configuration
            self.update_connection_config(connection)
            
            # Store the data values for this connection
            if connection_id not in self.tag_values:
                self.tag_values[connection_id] = {}
            
            # Update tag values from packet data
            for tag_label, tag_info in packet_data.items():
                if isinstance(tag_info, dict) and 'value' in tag_info:
                    self.tag_values[connection_id][tag_label] = tag_info['value']
                    # print(f"  Updated tag '{tag_label}' with value: {tag_info['value']}")
            
            # print(f"  Current tag_values for connection {connection_id}: {self.tag_values[connection_id]}")
            
            # Update memory areas with tag values
            self._update_memory_areas(connection_id, connection)
            
            self.packet_count += 1
            if self.packet_count % 20 == 0:
                print(f"S7 packet processed successfully. Total packets: {self.packet_count}")
            
        except Exception as e:
            print(f"Error processing S7 data: {e}")
            import traceback
            traceback.print_exc()

    def _update_memory_areas(self, connection_id: int, connection: Dict[str, Any]):
        """Update memory areas based on connection tags"""
        try:
            tags = connection.get('tags', [])
            # print(f"Updating memory areas for connection {connection_id} with {len(tags)} tags")
            
            for i, tag in enumerate(tags):
                if not tag.get('enabled', True):
                    print(f"  Tag {i}: {tag.get('label')} - disabled, skipping")
                    continue
                
                label = tag.get('label')
                area = tag.get('area')
                db_number = tag.get('db_number')
                byte_offset = tag.get('byte_offset', 0)
                bit_offset = tag.get('bit_offset')
                data_type = tag.get('data_type')
                length = tag.get('length', 1)
                
                # print(f"  Tag {i}: {label} -> {area}{db_number if db_number else ''}.{byte_offset} ({data_type})")
                
                # Get the value from tag_values or use default
                value = self.tag_values.get(connection_id, {}).get(label)
                
                if value is None:
                    # Use default values based on data type
                    value = self._get_default_value(data_type)
                    print(f"    Using default value: {value}")
                # else:
                    # print(f"    Using provided value: {value}")
                
                # Write to appropriate memory area
                if area == 'DB' and db_number is not None:
                    self._write_to_db(db_number, byte_offset, bit_offset, data_type, length, value, tag)
                    # print(f"    Written to DB{db_number}")
                elif area == 'M':
                    self._write_to_memory_area(self.mk_area, byte_offset, bit_offset, data_type, length, value, tag)
                    # print(f"    Written to Merker area")
                elif area == 'I':
                    self._write_to_memory_area(self.pe_area, byte_offset, bit_offset, data_type, length, value, tag)
                    # print(f"    Written to Inputs area")
                elif area == 'Q':
                    self._write_to_memory_area(self.pa_area, byte_offset, bit_offset, data_type, length, value, tag)
                    # print(f"    Written to Outputs area")
                
                self.update_count += 1
                
            #  print(f"Memory areas updated. Total updates: {self.update_count}")
                
        except Exception as e:
            print(f"Error updating memory areas for connection {connection_id}: {e}")
            import traceback
            traceback.print_exc()

    def _write_to_db(self, db_number: int, byte_offset: int, bit_offset: int, 
                     data_type: str, length: int, value: Any, tag: Dict[str, Any]):
        """Write value to DB area"""
        try:
            if db_number not in self.db_areas:
                # Create DB if it doesn't exist
                size = max(256, byte_offset + length + 16)
                size = ((size + 255) // 256) * 256
                self.db_areas[db_number] = (c_ubyte * size)()
                print(f"Created DB{db_number} with size {size}")
                
                # Register the new DB with server
                if self.server:
                    self.server.register_area(SrvArea.DB, db_number, self.db_areas[db_number])
                    print(f"Registered DB{db_number} with S7 server")
            
            db_area = self.db_areas[db_number]
            self._write_tag_value(db_area, byte_offset, bit_offset, data_type, length, value, tag)
            # print(f"Successfully wrote to DB{db_number}")
            
        except Exception as e:
            print(f"Error writing to DB{db_number}: {e}")
            import traceback
            traceback.print_exc()

    def _write_to_memory_area(self, area: ctypes.Array, byte_offset: int, bit_offset: int,
                             data_type: str, length: int, value: Any, tag: Dict[str, Any]):
        """Write value to memory area (M, I, Q)"""
        try:
            self._write_tag_value(area, byte_offset, bit_offset, data_type, length, value, tag)
        except Exception as e:
            print(f"Error writing to memory area: {e}")

    def _write_tag_value_to_bytearray(self, area: bytearray, byte_offset: int, bit_offset: int,
                                      data_type: str, length: int, value: Any, tag: Dict[str, Any]):
        """Write a tag value to a bytearray (server's internal memory)."""
        if data_type == 'BOOL':
            if bit_offset is None:
                raise ValueError("BOOL type requires bit_offset")
            current_byte = area[byte_offset]
            if value:
                current_byte |= (1 << bit_offset)
            else:
                current_byte &= ~(1 << bit_offset)
            area[byte_offset] = current_byte
        elif data_type == 'BYTE':
            area[byte_offset] = value & 0xFF
        elif data_type == 'CHAR':
            area[byte_offset] = ord(value[0]) & 0xFF if isinstance(value, str) and len(value) > 0 else 0
        elif data_type in ['WORD', 'INT']:
            if data_type == 'WORD':
                value = value & 0xFFFF
            else:
                value = max(-32768, min(32767, value))
                if value < 0:
                    value = 65536 + value
            area[byte_offset] = (value >> 8) & 0xFF
            area[byte_offset + 1] = value & 0xFF
        elif data_type in ['DWORD', 'DINT']:
            if data_type == 'DWORD':
                value = value & 0xFFFFFFFF
            else:
                value = max(-2147483648, min(2147483647, value))
                if value < 0:
                    value = 4294967296 + value
            area[byte_offset] = (value >> 24) & 0xFF
            area[byte_offset + 1] = (value >> 16) & 0xFF
            area[byte_offset + 2] = (value >> 8) & 0xFF
            area[byte_offset + 3] = value & 0xFF
        elif data_type == 'REAL':
            float_bytes = struct.pack('>f', float(value))
            for i, byte_val in enumerate(float_bytes):
                area[byte_offset + i] = byte_val
        elif data_type == 'STRING':
            max_length = length - 2
            if isinstance(value, str):
                actual_length = min(len(value), max_length)
                area[byte_offset] = max_length
                area[byte_offset + 1] = actual_length
                for i in range(actual_length):
                    area[byte_offset + 2 + i] = ord(value[i])
            else:
                area[byte_offset] = max_length
                area[byte_offset + 1] = 0

    def _write_tag_value(self, area: ctypes.Array, byte_offset: int, bit_offset: int,
                        data_type: str, length: int, value: Any, tag: Dict[str, Any]):
        """Write a tag value to the specified memory area"""
        try:
            if data_type == 'BOOL':
                if bit_offset is None:
                    raise ValueError("BOOL type requires bit_offset")
                # Write boolean to specific bit
                current_byte = area[byte_offset]
                if value:
                    current_byte |= (1 << bit_offset)
                else:
                    current_byte &= ~(1 << bit_offset)
                area[byte_offset] = current_byte
                
            elif data_type == 'BYTE':
                area[byte_offset] = value & 0xFF
                
            elif data_type == 'CHAR':
                if isinstance(value, str) and len(value) > 0:
                    area[byte_offset] = ord(value[0]) & 0xFF
                else:
                    area[byte_offset] = 0
                    
            elif data_type in ['WORD', 'INT']:
                if data_type == 'WORD':
                    value = value & 0xFFFF
                else:  # INT (signed)
                    value = max(-32768, min(32767, value))
                    if value < 0:
                        value = 65536 + value
                
                # Write as big-endian (S7 standard)
                area[byte_offset] = (value >> 8) & 0xFF
                area[byte_offset + 1] = value & 0xFF
                
            elif data_type in ['DWORD', 'DINT']:
                if data_type == 'DWORD':
                    value = value & 0xFFFFFFFF
                else:  # DINT (signed)
                    value = max(-2147483648, min(2147483647, value))
                    if value < 0:
                        value = 4294967296 + value
                
                # Write as big-endian
                area[byte_offset] = (value >> 24) & 0xFF
                area[byte_offset + 1] = (value >> 16) & 0xFF
                area[byte_offset + 2] = (value >> 8) & 0xFF
                area[byte_offset + 3] = value & 0xFF
                
            elif data_type == 'REAL':
                # Convert float to bytes (IEEE 754)
                float_bytes = struct.pack('>f', float(value))  # Big-endian
                for i, byte_val in enumerate(float_bytes):
                    area[byte_offset + i] = byte_val
                    
            elif data_type == 'STRING':
                # S7 strings have first byte as max length, second as actual length
                max_length = length - 2  # Reserve 2 bytes for length info
                if isinstance(value, str):
                    actual_length = min(len(value), max_length)
                    area[byte_offset] = max_length
                    area[byte_offset + 1] = actual_length
                    for i in range(actual_length):
                        area[byte_offset + 2 + i] = ord(value[i])
                else:
                    area[byte_offset] = max_length
                    area[byte_offset + 1] = 0
                    
            else:
                print(f"Unsupported data type: {data_type}")
                
        except Exception as e:
            print(f"Error writing tag value {tag.get('label')}: {e}")

    def _get_default_value(self, data_type: str) -> Any:
        """Get default value for data type"""
        defaults = {
            'BOOL': False,
            'BYTE': 0,
            'CHAR': '\x00',
            'WORD': 0,
            'INT': 0,
            'DWORD': 0,
            'DINT': 0,
            'REAL': 0.0,
            'STRING': ''
        }
        return defaults.get(data_type, 0)

    def update_tag_value(self, connection_id: int, tag_label: str, value: Any):
        """Update a specific tag value (for testing or manual updates)"""
        if connection_id not in self.tag_values:
            self.tag_values[connection_id] = {}
        self.tag_values[connection_id][tag_label] = value

    def get_statistics(self) -> Dict[str, Any]:
        """Get server statistics"""
        return {
            'running': self.running,
            'tcp_port': self.tcp_port,
            'packet_count': self.packet_count,
            'update_count': self.update_count,
            'connections': len(self.connections),
            'db_areas': len(self.db_areas)
        }

    def set_tag_value(self, connection_id: int, tag_label: str, value: Any):
        """Set a specific tag value (for external updates)"""
        if connection_id not in self.tag_values:
            self.tag_values[connection_id] = {}
        self.tag_values[connection_id][tag_label] = value
        
        # Trigger update if server is running
        if self.running and connection_id in self.connections:
            self._update_memory_areas(connection_id, self.connections[connection_id])