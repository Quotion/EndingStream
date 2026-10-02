obs = obslua

local function script_dir()
    local source = debug.getinfo(1, "S").source
    if source:sub(1, 1) == "@" then
        source = source:sub(2)
    end
    return source:match("^(.*[\\/])") or ".\\"
end

local function quote(value)
    return '"' .. value:gsub('"', '""') .. '"'
end

local function start_ending()
    local launcher = script_dir() .. "start-ending-hidden.vbs"
    local command = "wscript.exe //B //Nologo " .. quote(launcher)
    os.execute(command)
    obs.script_log(obs.LOG_INFO, "Ending hidden launcher was called.")
end

function script_description()
    return "Starts the local Ending backend hidden when OBS loads this script."
end

function script_load(settings)
    start_ending()
end

function script_properties()
    local props = obs.obs_properties_create()
    obs.obs_properties_add_button(props, "start_now", "Start Ending now", function()
        start_ending()
        return true
    end)
    return props
end
