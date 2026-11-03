"""L12 requires CCSC fields and high-risk controls, unlike NIO's T12."""

BINDINGS = {
    "v4": {
        "L13:1:d9d7fb687737a02a84aa": "rule_1",
        "L13:2:59b8e13ae179a2593077": "rule_2",
        "L12:1:c437062e97210ecb39ba": "rule_1",
        "L12:2:9344878425e7576fad48": "rule_2",
        "L12:3:d96790310a80ac04b9a3": "rule_3",
    },
}
