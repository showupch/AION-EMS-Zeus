"""Minimal fail-closed Modbus TCP client for my-PV ELWA direct control.

Zeus intentionally implements only the two Modbus operations required by the
validated ELWA path:
- FC03 read holding register(s)
- FC06 write single holding register

The implementation uses a fresh TCP connection per transaction.  That keeps the
runtime stateless, avoids background socket lifecycle problems during Home
Assistant reloads, and makes every command independently timeout-bounded.
"""
from __future__ import annotations

import asyncio
import ipaddress
import struct
from dataclasses import dataclass


class ElwaModbusError(RuntimeError):
    """Raised when a direct ELWA Modbus transaction cannot be validated."""


@dataclass(frozen=True)
class ElwaDirectTarget:
    host: str
    port: int = 502
    unit: int = 1
    power_register: int = 1000
    temperature_register: int = 1001
    target_temperature_register: int = 1002
    timeout_s: float = 5.0

    @classmethod
    def from_host(cls, host: str) -> "ElwaDirectTarget":
        value = str(host or "").strip()
        if not value:
            raise ElwaModbusError("ELWA IP address is not configured")
        try:
            # Direct mode deliberately accepts an IP literal only.  This avoids
            # ambiguous DNS/URL input and matches the simple Zeus setup contract.
            ipaddress.ip_address(value)
        except ValueError as err:
            raise ElwaModbusError("ELWA address must be a valid IPv4 or IPv6 address") from err
        return cls(host=value)


class ElwaDirectModbusClient:
    """Small Modbus TCP transaction helper for a single ELWA target."""

    def __init__(self, target: ElwaDirectTarget) -> None:
        self.target = target
        self._transaction_id = 0
        self._lock = asyncio.Lock()

    def _next_transaction_id(self) -> int:
        self._transaction_id = (self._transaction_id + 1) & 0xFFFF
        if self._transaction_id == 0:
            self._transaction_id = 1
        return self._transaction_id

    async def _exchange(self, pdu: bytes, *, expected_function: int) -> bytes:
        async with self._lock:
            transaction_id = self._next_transaction_id()
            unit = int(self.target.unit)
            # MBAP length counts unit-id + PDU.
            request = struct.pack(">HHHB", transaction_id, 0, len(pdu) + 1, unit) + pdu
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.target.host, int(self.target.port)),
                    timeout=float(self.target.timeout_s),
                )
                writer.write(request)
                await asyncio.wait_for(writer.drain(), timeout=float(self.target.timeout_s))
                header = await asyncio.wait_for(reader.readexactly(7), timeout=float(self.target.timeout_s))
                rx_transaction, protocol_id, length, rx_unit = struct.unpack(">HHHB", header)
                if rx_transaction != transaction_id:
                    raise ElwaModbusError("ELWA Modbus transaction id mismatch")
                if protocol_id != 0:
                    raise ElwaModbusError("ELWA Modbus protocol id is not zero")
                if rx_unit != unit:
                    raise ElwaModbusError("ELWA Modbus unit id mismatch")
                if length < 2 or length > 260:
                    raise ElwaModbusError("ELWA Modbus response length is invalid")
                response_pdu = await asyncio.wait_for(
                    reader.readexactly(length - 1), timeout=float(self.target.timeout_s)
                )
            except ConnectionRefusedError as err:
                raise ElwaModbusError(
                    f"ELWA Modbus TCP connection refused by {self.target.host}:{self.target.port}"
                ) from err
            except asyncio.TimeoutError as err:
                raise ElwaModbusError(
                    f"ELWA Modbus TCP timeout communicating with {self.target.host}:{self.target.port}"
                ) from err
            except asyncio.IncompleteReadError as err:
                raise ElwaModbusError(
                    f"ELWA Modbus TCP connection closed before a complete response was received "
                    f"from {self.target.host}:{self.target.port}"
                ) from err
            except OSError as err:
                detail = str(err).strip() or err.__class__.__name__
                raise ElwaModbusError(
                    f"ELWA Modbus TCP socket error to {self.target.host}:{self.target.port}: {detail}"
                ) from err
            finally:
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except Exception:
                        pass

            if not response_pdu:
                raise ElwaModbusError("ELWA Modbus response is empty")
            function = response_pdu[0]
            if function == (expected_function | 0x80):
                code = response_pdu[1] if len(response_pdu) > 1 else -1
                raise ElwaModbusError(f"ELWA Modbus exception response {code}")
            if function != expected_function:
                raise ElwaModbusError(
                    f"ELWA Modbus function mismatch: expected {expected_function}, got {function}"
                )
            return response_pdu

    async def read_holding_register(self, address: int) -> int:
        address = int(address)
        if not 0 <= address <= 0xFFFF:
            raise ElwaModbusError("ELWA register address is out of range")
        pdu = struct.pack(">BHH", 3, address, 1)
        response = await self._exchange(pdu, expected_function=3)
        if len(response) != 4 or response[1] != 2:
            raise ElwaModbusError("ELWA read-register response has an invalid payload")
        return int(struct.unpack(">H", response[2:4])[0])

    async def write_holding_register(self, address: int, value: int) -> None:
        address = int(address)
        value = int(value)
        if not 0 <= address <= 0xFFFF:
            raise ElwaModbusError("ELWA register address is out of range")
        if not 0 <= value <= 0xFFFF:
            raise ElwaModbusError("ELWA register value is out of range")
        pdu = struct.pack(">BHH", 6, address, value)
        response = await self._exchange(pdu, expected_function=6)
        if len(response) != 5:
            raise ElwaModbusError("ELWA write-register response has an invalid payload")
        rx_address, rx_value = struct.unpack(">HH", response[1:5])
        if rx_address != address or rx_value != value:
            raise ElwaModbusError("ELWA write-register echo does not match the request")

    async def read_snapshot(self) -> dict[str, float | int]:
        """Read the standard ELWA live registers used by Zeus."""
        power_raw = await self.read_holding_register(self.target.power_register)
        temperature_raw = await self.read_holding_register(self.target.temperature_register)
        return {
            "power_w": int(power_raw),
            # my-PV ELWA register 1001 uses a 1/10 °C scaling factor.
            "temperature_c": round(float(temperature_raw) * 0.1, 1),
        }
