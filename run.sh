#!/usr/bin/env bash
set -uo pipefail

# Mesa & RADV APU Optimisations
# Note: nogttspill is disabled to allow GTT spilling into system memory under pressure,
# preventing hard -ENOMEM command submission rejections that crash GNOME Shell / Wayland.
# export RADV_PERFTEST=nogttspill
export radv_enable_unified_heap_on_apu=true
# Cap single Vulkan buffer allocations at 3GB to prevent GTT OOM panics on 16GB RAM
export GGML_VK_FORCE_MAX_ALLOCATION_SIZE=3221225472
# gfx90c APU: split every Vulkan submission group into single-node submits so no
# compute job outlives the 2s amdgpu lockup_timeout (llama.cpp #21724). Keeps
# reason-fast/Ornith deep-KV prefills alive without touching kernel boot params.
export GGML_VK_MAX_NODES_PER_SUBMIT=1
# Uncomment below to run completely offline without checking Hugging Face manifests:
# export LLAMA_OFFLINE=1

asus_policy="/sys/devices/platform/asus-nb-wmi/throttle_thermal_policy"

restore_settings() {
    echo ""
    echo "[llama-thermal-guard] Restoring default hardware performance settings..."

    # Restore CPU boost
    if [[ -f /sys/devices/system/cpu/cpufreq/boost ]]; then
        echo 1 | sudo tee /sys/devices/system/cpu/cpufreq/boost >/dev/null 2>&1 || true
    fi
    for cpu_boost in /sys/devices/system/cpu/cpu*/cpufreq/boost; do
        if [[ -f "$cpu_boost" ]]; then
            echo 1 | sudo tee "$cpu_boost" >/dev/null 2>&1 || true
        fi
    done

    # Restore EPP
    for epp in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do
        if [[ -f "$epp" ]]; then
            echo "balance_performance" | sudo tee "$epp" >/dev/null 2>&1 || true
        fi
    done

    # Restore ACPI profile (also resets asus-nb-wmi throttle_thermal_policy to 0/balanced)
    if [[ -f /sys/firmware/acpi/platform_profile ]] && [[ -r /sys/firmware/acpi/platform_profile_choices ]]; then
        choices=$(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null || echo "")
        if [[ "$choices" == *"balanced"* ]]; then
            echo "balanced" | sudo tee /sys/firmware/acpi/platform_profile >/dev/null 2>&1 || true
        fi
    elif [[ -f "$asus_policy" ]]; then
        echo 0 | sudo tee "$asus_policy" >/dev/null 2>&1 || true
    fi

    # Restore amdgpu ring job timeout to driver default (0 = legacy ~2s detection)
    if [[ -f /sys/module/amdgpu/parameters/timeout_period ]]; then
        echo 0 | sudo tee /sys/module/amdgpu/parameters/timeout_period >/dev/null 2>&1 || true
    fi

    # Restore Vega 7 OverDrive clock table and DPM mode
    for dev in /sys/class/drm/card*/device; do
        if [[ -f "$dev/pp_od_clk_voltage" ]]; then
            echo "r" | sudo tee "$dev/pp_od_clk_voltage" >/dev/null 2>&1 || true
            echo "c" | sudo tee "$dev/pp_od_clk_voltage" >/dev/null 2>&1 || true
        fi
        if [[ -f "$dev/power_dpm_force_performance_level" ]]; then
            echo "auto" | sudo tee "$dev/power_dpm_force_performance_level" >/dev/null 2>&1 || true
        fi
    done

    echo "CPU Boost:        $(cat /sys/devices/system/cpu/cpufreq/boost 2>/dev/null || echo 'N/A')"
    echo "AMD P-State EPP:  $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null || echo 'N/A')"
    echo "ACPI Profile:     $(cat /sys/firmware/acpi/platform_profile 2>/dev/null || echo 'N/A')"
    echo "Asus Fan Policy:  $(cat "$asus_policy" 2>/dev/null || echo 'N/A')"
    echo "Vega 7 DPM Mode:  $(cat /sys/class/drm/card*/device/power_dpm_force_performance_level 2>/dev/null || echo 'N/A')"
    echo "Vega 7 SCLK:      $(tr '\n' ' ' < /sys/class/drm/card1/device/pp_dpm_sclk 2>/dev/null || echo 'N/A')"
    echo "Vega 7 OD SCLK:   $(awk '/^[01]:/{printf "%s %s  ", $1, $2}' /sys/class/drm/card1/device/pp_od_clk_voltage 2>/dev/null || echo 'N/A')"
    echo "GPU Ring Timeout: $(cat /sys/module/amdgpu/parameters/timeout_period 2>/dev/null || echo 'N/A')s"
}

# Pre-flight check: Refuse to run if llama-server was installed without Vulkan GPU support
if ! llama-server --list-devices 2>/dev/null | grep -q "Vulkan"; then
    echo "[llama-thermal-guard] ERROR: llama-server has no Vulkan backend (CPU-only build detected)!" >&2
    echo "Run: mise install -f llama.cpp" >&2
    exit 1
fi

# Trap signals and exit to ensure settings are always restored once
trap restore_settings EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

echo "[llama-thermal-guard] Applying safe thermal safeguards with root permissions..."

# 1. Safely disable CPU boost (caps 5600H at 3.3GHz base clock, cutting voltage spikes)
if [[ -f /sys/devices/system/cpu/cpufreq/boost ]]; then
    echo 0 | sudo tee /sys/devices/system/cpu/cpufreq/boost >/dev/null 2>&1 || true
fi
for cpu_boost in /sys/devices/system/cpu/cpu*/cpufreq/boost; do
    if [[ -f "$cpu_boost" ]]; then
        echo 0 | sudo tee "$cpu_boost" >/dev/null 2>&1 || true
    fi
done

# 2. Safely apply amd-pstate-epp profile (prefer balance_power over power to avoid starving tokenisation/n-gram CPU tasks)
for epp in /sys/devices/system/cpu/cpu*/cpufreq/energy_performance_preference; do
    if [[ -f "$epp" ]]; then
        avail_epp=$(cat "${epp%/*}/energy_performance_available_preferences" 2>/dev/null || echo "")
        if [[ "$avail_epp" == *"balance_power"* ]]; then
            echo "balance_power" | sudo tee "$epp" >/dev/null 2>&1 || true
        else
            echo "power" | sudo tee "$epp" >/dev/null 2>&1 || true
        fi
    fi
done

# 3. Keep ACPI platform profile on balanced (preserves active fan cooling while CPU/iGPU clocks are capped)
if [[ -f /sys/firmware/acpi/platform_profile ]] && [[ -r /sys/firmware/acpi/platform_profile_choices ]]; then
    choices=$(cat /sys/firmware/acpi/platform_profile_choices 2>/dev/null || echo "")
    if [[ "$choices" == *"balanced"* ]]; then
        echo "balanced" | sudo tee /sys/firmware/acpi/platform_profile >/dev/null 2>&1 || true
    fi
elif [[ -f "$asus_policy" ]]; then
    echo 0 | sudo tee "$asus_policy" >/dev/null 2>&1 || true
fi

# 4. Safely cap AMD Radeon iGPU SoftMaxGfxClk to 1500 MHz via OverDrive (pp_od_clk_voltage)
# Avoids the 700 MHz state-1 bottleneck (~2.14x faster prefill) while cutting top-end 1800 MHz voltage/power spikes
for dev in /sys/class/drm/card*/device; do
    if [[ -f "$dev/power_dpm_force_performance_level" ]] && [[ -f "$dev/pp_od_clk_voltage" ]]; then
        echo "manual" | sudo tee "$dev/power_dpm_force_performance_level" >/dev/null 2>&1 || true
        echo "s 0 200" | sudo tee "$dev/pp_od_clk_voltage" >/dev/null 2>&1 || true
        echo "s 1 1500" | sudo tee "$dev/pp_od_clk_voltage" >/dev/null 2>&1 || true
        echo "c" | sudo tee "$dev/pp_od_clk_voltage" >/dev/null 2>&1 || true
    fi
done

# 5. Raise the amdgpu compute-ring job timeout (timeout_period, in seconds; 0 = legacy ~2s)
# Vega 7's comp ring kills any Vulkan job running longer than ~2s. Ling-3.0-tiny MoE prefill
# with a deep cached KV (>25k) runs single flash-attention submits of 3-5s on the 7-CU iGPU,
# so the kernel resets the ring and RADV surfaces "device lost" -> llama-server exits
# (llama.cpp #21724, gfx90c APUs; rings reset but the app dies). 30s covers up to 64k KV with
# margin. lockup_timeout (ms) is read-only at runtime on this kernel; timeout_period is not.
# Compute-ring resets are scoped to that ring, so the desktop compositor is unaffected.
if [[ -f /sys/module/amdgpu/parameters/timeout_period ]]; then
    echo 30 | sudo tee /sys/module/amdgpu/parameters/timeout_period >/dev/null 2>&1 || true
fi

echo "CPU Boost:        $(cat /sys/devices/system/cpu/cpufreq/boost 2>/dev/null || echo 'N/A')"
echo "AMD P-State EPP:  $(cat /sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference 2>/dev/null || echo 'N/A')"
echo "ACPI Profile:     $(cat /sys/firmware/acpi/platform_profile 2>/dev/null || echo 'N/A')"
echo "Asus Fan Policy:  $(cat "$asus_policy" 2>/dev/null || echo 'N/A')"
echo "Vega 7 DPM Mode:  $(cat /sys/class/drm/card*/device/power_dpm_force_performance_level 2>/dev/null || echo 'N/A')"
echo "Vega 7 SCLK:      $(tr '\n' ' ' < /sys/class/drm/card1/device/pp_dpm_sclk 2>/dev/null || echo 'N/A')"
echo "Vega 7 OD SCLK:   $(awk '/^[01]:/{printf "%s %s  ", $1, $2}' /sys/class/drm/card1/device/pp_od_clk_voltage 2>/dev/null || echo 'N/A')"
echo "GPU Ring Timeout: $(cat /sys/module/amdgpu/parameters/timeout_period 2>/dev/null || echo 'N/A')s"

# Run llama.cpp
llama-server --webui --models-preset models.ini --models-max 1 --host 0.0.0.0 --port 8081
