-- Evaluate the source DIF room and surface data retained by the map importer.
local acoustic = {}
local side_epsilon = 0.005
local portal_falloff_m = 10

local function distance(plane, point)
    return plane[1] * point[1] + plane[2] * point[2] + plane[3] * point[3] + plane[4]
end

local function point_on_segment(start, finish, fraction)
    return {
        start[1] + (finish[1] - start[1]) * fraction,
        start[2] + (finish[2] - start[2]) * fraction,
        start[3] + (finish[3] - start[3]) * fraction,
    }
end

function acoustic.prepare(vertices, data)
    local collision = data.collision
    local low, high = {math.huge, math.huge, math.huge}, {-math.huge, -math.huge, -math.huge}
    for _, point in ipairs(vertices) do
        for axis = 1, 3 do
            low[axis] = math.min(low[axis], point[axis])
            high[axis] = math.max(high[axis], point[axis])
        end
    end
    local splits = {}
    for index, room_plane in ipairs(data.nodes) do
        local encoded = collision.node_planes[index]
        local direction = encoded & 0x8000 ~= 0 and -1 or 1
        splits[index] = {
            room_plane[1] * direction, room_plane[2] * direction, room_plane[3] * direction,
            room_plane[4] * direction, id = encoded & 0x7fff,
        }
    end
    return {vertices = vertices, data = data, splits = splits, low = low, high = high}
end

local function crosses_bounds(model, start, finish, limit)
    local first, last = 0, limit
    for axis = 1, 3 do
        local low, high = model.low[axis], model.high[axis]
        local origin, delta = start[axis], finish[axis] - start[axis]
        if delta == 0 then
            if origin < low or origin > high then
                return false
            end
        else
            local enter, exit = (low - origin) / delta, (high - origin) / delta
            first = math.max(first, math.min(enter, exit))
            last = math.min(last, math.max(enter, exit))
            if first > last then
                return false
            end
        end
    end
    return true
end

local function side(value)
    if value >= side_epsilon then
        return 1
    end
    return value <= -side_epsilon and -1 or 0
end

local function matching_face(model, leaf, split, hit)
    if not split then
        return nil
    end
    local collision = model.data.collision
    for _, surface in ipairs(collision.leaves[leaf + 1]) do
        local fan = collision.faces[surface + 1]
        local plane = fan[1]
        if plane & 0x7fff == split.id then
            local direction = plane & 0x8000 ~= 0 and -1 or 1
            local contained = true
            for index = 2, #fan do
                local a = model.vertices[fan[index] + 1]
                local b = model.vertices[fan[index == #fan and 2 or index + 1] + 1]
                local x, y, z = hit[1] - a[1], hit[2] - a[2], hit[3] - a[3]
                local u, v, w = b[1] - a[1], b[2] - a[2], b[3] - a[3]
                local edge = split[1] * (y * w - z * v) + split[2] * (z * u - x * w) + split[3] * (x * v - y * u)
                if edge * direction < 0 then
                    contained = false
                    break
                end
            end
            if contained then
                return surface
            end
        end
    end
    return nil
end

local function trace(model, start, finish, limit)
    if not crosses_bounds(model, start, finish, limit) then
        return nil
    end
    local data = model.data
    local stack = {{0, -1, 0, 1}}
    local function push(node, branch, first, last, entered_at)
        local child = data.nodes[node][branch + 4]
        local leaf = data.collision.solid_children[node][branch]
        if first <= limit and (child >= 0 or leaf >= 0) then
            stack[#stack + 1] = {child, leaf, first, last, entered_at}
        end
    end
    local visited = 0
    while #stack > 0 do
        local segment = table.remove(stack)
        local index, leaf, first, last, entered_at = table.unpack(segment)
        if leaf >= 0 then
            local hit = point_on_segment(start, finish, first)
            return first, matching_face(model, leaf, entered_at, hit)
        end
        visited = visited + 1
        if visited > #model.splits * 4 then
            -- A malformed tree can revisit the same branch many times.
            -- Preserve the previous gain if the trace is inconclusive.
            return 0
        end
        local node = index + 1
        local plane = model.splits[node]
        local origin = distance(plane, start)
        local delta = distance(plane, finish) - origin
        local a = origin + first * delta
        local b = origin + last * delta
        local start_side, end_side = side(a), side(b)
        if start_side == 0 and end_side == 0 then
            if data.nodes[node][5] >= 0 then
                push(node, 1, first, last, entered_at)
            end
            if data.nodes[node][6] >= 0 then
                push(node, 2, first, last, entered_at)
            end
        elseif start_side * end_side >= 0 then
            push(node, start_side + end_side > 0 and 1 or 2, first, last, entered_at)
        else
            local split = first + (last - first) * (a / (a - b))
            local near = start_side > 0 and 1 or 2
            push(node, 3 - near, split, last, plane)
            push(node, near, first, split, entered_at)
        end
    end
    return nil
end

local function room_gain(data, listener)
    local index = 0
    while index >= 0 do
        local node = data.nodes[index + 1]
        index = node[distance(node, listener) >= 0 and 5 or 6]
    end
    if index == -2 then
        return 1
    end
    if index == -1 then
        return 0
    end
    local gain = 0
    for _, portal in ipairs(data.portal_centers_m[-index - 1]) do
        local x, y, z = listener[1] - portal[1], listener[2] - portal[2], listener[3] - portal[3]
        gain = gain + math.max(0, 1 - math.sqrt(x * x + y * y + z * z) / portal_falloff_m)
    end
    return math.min(1, gain)
end

-- Each placement shares a prepared model and transforms the same world ray
-- into its own source-local metres. Segment fractions stay comparable.
function acoustic.gain(placements, start, finish, previous)
    local nearest, face, model, listener
    for _, placement in ipairs(placements) do
        local first, last = placement.localize(start), placement.localize(finish)
        local fraction, surface = trace(placement.model, first, last, nearest or 1)
        if fraction and (not nearest or fraction < nearest) then
            nearest, face, model, listener = fraction, surface, placement.model, first
        end
    end
    if not nearest then
        return 1
    end
    if face == nil then
        return previous
    end
    if model.data.outside_surfaces[face + 1] then
        return 1
    end
    return room_gain(model.data, listener)
end

function acoustic.listener(placements, tracks, cm_per_m)
    local previous
    return function(camera)
        local finish = {camera[1], camera[2], camera[3] - 2000 * cm_per_m}
        local gain = acoustic.gain(placements, camera, finish, previous or 1)
        for _, track in ipairs(tracks) do
            local angular = track.angular_gain and track.angular_gain(camera) or 1
            local volume = track.source_gain * gain * angular
            if volume ~= track.last_volume then
                track.set_gain(volume)
                track.last_volume = volume
            end
        end
        previous = gain
        return gain
    end
end

local function indexed_actors(env, world, type_name, rows)
    local found = {}
    for _, actor in ipairs(env.find(type_name) or {}) do
        if env.valid(actor) and env.valid(actor:GetWorld()) and actor:GetWorld():GetAddress() == world then
            for _, row in ipairs(rows) do
                local tag = row[1]
                if actor:ActorHasTag(env.name(tag)) then
                    assert(not found[tag], "Duplicate outside-only audio binding: " .. tag)
                    found[tag] = actor
                end
            end
        end
    end
    return found
end

function acoustic.bind(env, world, document)
    local cm_per_m, room_rows, model_rows, emitter_rows = table.unpack(document)
    local room_actors = indexed_actors(env, world, "StaticMeshActor", room_rows)
    local sound_actors = indexed_actors(env, world, "AmbientSound", emitter_rows)
    local models, placements, tracks = {}, {}, {}
    for index, row in ipairs(model_rows) do
        models[index] = acoustic.prepare(row[1], {
            nodes = row[2], outside_surfaces = row[3], portal_centers_m = row[4],
            collision = {node_planes = row[5], solid_children = row[6], leaves = row[7], faces = row[8]},
        })
    end
    for _, row in ipairs(room_rows) do
        local actor = room_actors[row[1]]
        if not actor then return nil end
        placements[#placements + 1] = {model = models[row[2]], localize = function(point)
            local p = env.local_point(actor, point)
            return {p.X * .01, p.Y * -.01, p.Z * .01}
        end}
    end
    for _, row in ipairs(emitter_rows) do
        local actor = sound_actors[row[1]]
        local component = actor and actor.AudioComponent
        if not env.valid(component) or not env.valid(component.Sound) then return nil end
        local started = false
        local track = {source_gain = row[2], set_gain = function(value)
            component:SetVolumeMultiplier(value)
            if not started then
                started = true
                component:Play(0)
            end
        end}
        local inner, outer, floor = table.unpack(row[3])
        if inner then
            track.angular_gain = function(camera)
                local origin, forward = actor:K2_GetActorLocation(), actor:GetActorForwardVector()
                local x, y, z = camera[1] - origin.X, camera[2] - origin.Y, camera[3] - origin.Z
                local length = math.sqrt(x * x + y * y + z * z)
                local dot = length > 0 and (x * forward.X + y * forward.Y + z * forward.Z) / length or 0
                local angle = math.deg(math.acos(math.max(-1, math.min(1, dot))))
                if angle <= inner then return 1 end
                if angle >= outer then return floor end
                return 1 - (1 - floor) * (angle - inner) / (outer - inner)
            end
        end
        tracks[#tracks + 1] = track
    end
    return acoustic.listener(placements, tracks, cm_per_m)
end

return acoustic
