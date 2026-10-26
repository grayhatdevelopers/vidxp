/* VidXP's Premiere 23.x bridge. Keep this file ES3-compatible for ExtendScript. */
$._VIDXP = {
  stringify: function (value) {
    if (value === null) return "null";
    var type = typeof value;
    if (type === "string") {
      return '"' + value
        .replace(/\\/g, "\\\\")
        .replace(/\"/g, '\\"')
        .replace(/\r/g, "\\r")
        .replace(/\n/g, "\\n")
        .replace(/\t/g, "\\t") + '"';
    }
    if (type === "number" || type === "boolean") return String(value);
    var entries = [];
    var index;
    if (value instanceof Array) {
      for (index = 0; index < value.length; index += 1) {
        entries.push($._VIDXP.stringify(value[index]));
      }
      return "[" + entries.join(",") + "]";
    }
    for (var key in value) {
      if (value.hasOwnProperty(key)) {
        entries.push($._VIDXP.stringify(key) + ":" + $._VIDXP.stringify(value[key]));
      }
    }
    return "{" + entries.join(",") + "}";
  },

  result: function (operation) {
    try {
      return $._VIDXP.stringify({ ok: true, value: operation() });
    } catch (error) {
      return $._VIDXP.stringify({
        ok: false,
        error: error && error.message ? error.message : String(error)
      });
    }
  },

  readItem: function (item) {
    if (item.type === ProjectItemType.BIN || item.type === ProjectItemType.ROOT) {
      var children = [];
      for (var childIndex = 0; childIndex < item.children.numItems; childIndex += 1) {
        var child = $._VIDXP.readItem(item.children[childIndex]);
        if (child !== null) children.push(child);
      }
      return {
        kind: "bin",
        id: item.nodeId,
        name: item.name,
        children: children
      };
    }
    if (item.type !== ProjectItemType.CLIP && item.type !== ProjectItemType.FILE) return null;
    if (item.isSequence()) return null;
    if (item.isOffline()) {
      return {
        kind: "clip",
        id: item.nodeId,
        name: item.name,
        availability: "offline",
        detail: "Media is offline in Premiere."
      };
    }
    var mediaPath = item.getMediaPath();
    if (!mediaPath) {
      return {
        kind: "clip",
        id: item.nodeId,
        name: item.name,
        availability: "unavailable",
        detail: "Premiere did not return a file-backed media path."
      };
    }
    return {
      kind: "clip",
      id: item.nodeId,
      name: item.name,
      nativePath: mediaPath,
      availability: "ready"
    };
  },

  getLibrary: function () {
    return $._VIDXP.result(function () {
      if (!app.project) return null;
      var root = app.project.rootItem;
      var items = [];
      for (var index = 0; index < root.children.numItems; index += 1) {
        var node = $._VIDXP.readItem(root.children[index]);
        if (node !== null) items.push(node);
      }
      return {
        projectName: app.project.name,
        sequenceName: app.project.activeSequence ? app.project.activeSequence.name : null,
        items: items
      };
    });
  },

  getSelection: function () {
    return $._VIDXP.result(function () {
      var ids = [];
      var seen = {};
      var viewIds = app.getProjectViewIDs();
      for (var viewIndex = 0; viewIndex < viewIds.length; viewIndex += 1) {
        var selected = app.getProjectViewSelection(viewIds[viewIndex]);
        for (var itemIndex = 0; itemIndex < selected.length; itemIndex += 1) {
          var id = selected[itemIndex].nodeId;
          if (!seen[id]) {
            seen[id] = true;
            ids.push(id);
          }
        }
      }
      return ids;
    });
  }
};
