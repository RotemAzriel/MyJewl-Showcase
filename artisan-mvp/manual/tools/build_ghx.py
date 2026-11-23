"""
Minimal .ghx builder for a single GhPython Script component.

Usage:
    ghx_bytes = build_ghpython_ghx(
        script_source="a = 'hello'",
        inputs=[("x", "x", "item")],   # (name, nickname, access)
        outputs=[("a", "a")],
    )

The resulting bytes can be base64-encoded and sent to the
Rhino.Compute POST /grasshopper endpoint.
"""
import uuid
import base64


def _guid():
    return str(uuid.uuid4()).upper()


# GhPython Script (IronPython / GH1) component type GUID
GHPYTHON_GUID = "410755b1-224a-4c1e-a407-bf32fb45ea7e"

ACCESS = {"item": 0, "list": 1, "tree": 2}


def _param_chunk(tag, index, name, nickname, access, instance_guid):
    acc = ACCESS.get(access, 0)
    return f"""        <chunk name="{tag}" index="{index}">
          <items count="7">
            <item name="Name" type_name="gh_string" type_code="10">{name}</item>
            <item name="NickName" type_name="gh_string" type_code="10">{nickname}</item>
            <item name="Description" type_name="gh_string" type_code="10">Script Variable {name}</item>
            <item name="Optional" type_name="gh_bool" type_code="1">true</item>
            <item name="Source count" type_name="gh_int32" type_code="2">0</item>
            <item name="InstanceGuid" type_name="gh_guid" type_code="9">{instance_guid}</item>
            <item name="Access" type_name="gh_int32" type_code="2">{acc}</item>
          </items>
          <chunks count="1">
            <chunk name="Attributes">
              <items count="2">
                <item name="Bounds" type_name="gh_drawing_rectangleF" type_code="47">
                  <X>0</X><Y>0</Y><W>40</W><H>20</H>
                </item>
                <item name="Pivot" type_name="gh_drawing_pointF" type_code="44">
                  <X>0</X><Y>0</Y>
                </item>
              </items>
              <chunks count="0"/>
            </chunk>
          </chunks>
        </chunk>"""


def build_ghpython_ghx(script_source, inputs, outputs):
    """
    Build a minimal .ghx XML with one GhPython Script component.

    inputs:  list of (name, nickname, access) tuples
    outputs: list of (name, nickname) tuples
    Returns the XML as bytes.
    """
    doc_id = _guid()
    inst_id = _guid()

    # Escape script source for XML CDATA
    safe_script = script_source.replace("]]>", "]]]]><![CDATA[>")

    # Build parameter chunks
    input_chunks = []
    for i, (name, nick, access) in enumerate(inputs):
        input_chunks.append(_param_chunk("InputParam", i, name, nick, access, _guid()))

    output_chunks = []
    for i, (name, nick) in enumerate(outputs):
        output_chunks.append(_param_chunk("OutputParam", i, name, nick, "item", _guid()))

    all_param_chunks = "\n".join(input_chunks + output_chunks)
    n_inputs = len(inputs)
    n_outputs = len(outputs)
    n_param_chunks = n_inputs + n_outputs

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<Archive name="Root">
  <items count="1">
    <item name="ArchiveVersion" type_name="gh_version" type_code="80">
      <Major>1</Major>
      <Minor>0</Minor>
      <Revision>0007</Revision>
    </item>
  </items>
  <chunks count="1">
    <chunk name="Definition">
      <items count="2">
        <item name="plugin_version" type_name="gh_version" type_code="80">
          <Major>1</Major>
          <Minor>0</Minor>
          <Revision>0007</Revision>
        </item>
        <item name="DocumentID" type_name="gh_guid" type_code="9">{doc_id}</item>
      </items>
      <chunks count="2">
        <chunk name="Thumbnail">
          <items count="0"/>
          <chunks count="0"/>
        </chunk>
        <chunk name="Objects">
          <items count="1">
            <item name="ObjectCount" type_name="gh_int32" type_code="2">1</item>
          </items>
          <chunks count="1">
            <chunk name="Object" index="0">
              <items count="1">
                <item name="GUID" type_name="gh_guid" type_code="9">{GHPYTHON_GUID}</item>
              </items>
              <chunks count="1">
                <chunk name="Container">
                  <items count="9">
                    <item name="InstanceGuid" type_name="gh_guid" type_code="9">{inst_id}</item>
                    <item name="Name" type_name="gh_string" type_code="10">Python Script</item>
                    <item name="NickName" type_name="gh_string" type_code="10">Script</item>
                    <item name="Description" type_name="gh_string" type_code="10">GhPython Script</item>
                    <item name="Exposure" type_name="gh_int32" type_code="2">2</item>
                    <item name="Obsolete" type_name="gh_bool" type_code="1">false</item>
                    <item name="IconOverride" type_name="gh_drawing_bitmap" type_code="38" />
                    <item name="Locked" type_name="gh_bool" type_code="1">false</item>
                    <item name="Hidden" type_name="gh_bool" type_code="1">false</item>
                  </items>
                  <chunks count="2">
                    <chunk name="Attributes">
                      <items count="2">
                        <item name="Bounds" type_name="gh_drawing_rectangleF" type_code="47">
                          <X>100</X><Y>100</Y><W>150</W><H>44</H>
                        </item>
                        <item name="Pivot" type_name="gh_drawing_pointF" type_code="44">
                          <X>125</X><Y>122</Y>
                        </item>
                      </items>
                      <chunks count="0"/>
                    </chunk>
                    <chunk name="Script">
                      <items count="5">
                        <item name="Description" type_name="gh_string" type_code="10">GhPython Script</item>
                        <item name="HideCodeOutput" type_name="gh_bool" type_code="1">true</item>
                        <item name="MarshalOutGuids" type_name="gh_bool" type_code="1">true</item>
                        <item name="IsCluster" type_name="gh_bool" type_code="1">false</item>
                        <item name="ScriptSource" type_name="gh_string" type_code="10"><![CDATA[{safe_script}]]></item>
                      </items>
                      <chunks count="2">
                        <chunk name="ParameterData">
                          <items count="2">
                            <item name="InputCount" type_name="gh_int32" type_code="2">{n_inputs}</item>
                            <item name="OutputCount" type_name="gh_int32" type_code="2">{n_outputs}</item>
                          </items>
                          <chunks count="{n_param_chunks}">
{all_param_chunks}
                          </chunks>
                        </chunk>
                        <chunk name="AdditionalData">
                          <items count="0"/>
                          <chunks count="0"/>
                        </chunk>
                      </chunks>
                    </chunk>
                  </chunks>
                </chunk>
              </chunks>
            </chunk>
          </chunks>
        </chunk>
      </chunks>
    </chunk>
  </chunks>
</Archive>"""
    return xml.encode("utf-8")


def build_compute_payload(ghx_bytes, values=None):
    """
    Build the JSON payload for POST /grasshopper.
    values: list of {"ParamName": name, "InnerTree": {"{{0;0}}": [{"type": ..., "data": ...}]}}
    """
    import json
    algo = base64.b64encode(ghx_bytes).decode("ascii")
    payload = {"algo": algo, "pointer": None, "values": values or []}
    return json.dumps(payload)


if __name__ == "__main__":
    # Minimal echo test: input x (string), output a = x
    script = 'a = x if x is not None else "no_input"'
    ghx = build_ghpython_ghx(
        script_source=script,
        inputs=[("x", "x", "item")],
        outputs=[("a", "a")],
    )
    payload = build_compute_payload(
        ghx,
        values=[{
            "ParamName": "x",
            "InnerTree": {
                "{0;0}": [{"type": "System.String", "data": "hello_from_mac"}]
            }
        }]
    )
    print("GHX bytes:", len(ghx))
    print("Payload bytes:", len(payload))
    # Write ghx for inspection
    with open("/tmp/test_echo.ghx", "wb") as f:
        f.write(ghx)
    print("GHX written to /tmp/test_echo.ghx")
