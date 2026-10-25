#!/usr/bin/env bash
# Quick thermal check for Ryzen APU (CPU, Vega iGPU, Asus fan)

cpu=$(awk '{printf "%.1f", $1/1000}' /sys/class/hwmon/hwmon4/temp1_input 2>/dev/null || echo "N/A")
gpu=$(awk '{printf "%.1f", $1/1000}' /sys/class/hwmon/hwmon7/temp1_input 2>/dev/null || echo "N/A")
fan=$(cat /sys/class/hwmon/hwmon5/fan1_input 2>/dev/null || echo "N/A")
policy=$(cat /sys/devices/platform/asus-nb-wmi/throttle_thermal_policy 2>/dev/null || echo "N/A")

case "$policy" in
  0) mode="Balanced" ;;
  1) mode="Performance" ;;
  2) mode="Silent" ;;
  *) mode="$policy" ;;
esac

echo "CPU: ${cpu}°C  |  GPU: ${gpu}°C  |  Fan: ${fan} RPM  |  Profile: ${mode}"
