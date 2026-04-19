"""PLC communication layer.

Contains:
    modbus_client       AsyncModbusTcpClient wrapper (FC03 read + FC16 write only)
    register_map        GE register addresses + FLOAT32 byte-order gate
    register_scanner    Parses Register_List.xlsx into a RegisterCatalog
"""
