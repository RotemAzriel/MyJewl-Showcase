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
import base64
import uuid
from xml.sax.saxutils import escape


def _guid():
    return str(uuid.uuid4()).upper()


# GhPython Script (IronPython / GH1) component type GUID
GHPYTHON_GUID = "410755b1-224a-4c1e-a407-bf32fb45ea7e"
# Rhino 8's native Python 3 Script component.  This is the component used by
# RhinoCodePluginGH and is the executable script contract on the Azure Rhino 8
# Compute worker.  The legacy GhPython GUID above is retained for the small
# standalone legacy builder, but Phase C.1 uses this native component.
PYTHON3_GUID = "719467e6-7cf5-4848-99b0-c5dd57e5442c"
MODERN_OBJECT_HINT_GUID = "1c282eeb-dd16-439f-94e4-7d92b542fe8b"
# Grasshopper's built-in floating string parameter.  Unlike the GhPython
# component's internal ports, a top-level Param_String can be placed in an
# RH_IN group and is therefore visible to Rhino.Compute's Resthopper schema.
PARAM_STRING_GUID = "3EDE854E-C753-40EB-84CB-B48008F14FD4"
# Rhino 8 Python 3 Script variable parameter type GUID.  These IDs appear in
# ParameterData.InputId/OutputId in saved native definitions; the per-object
# InstanceGuid values remain unique and are stored in the parameter chunks.
SCRIPT_VARIABLE_PARAM_GUID = "08908df5-fa14-4982-9ab2-1aa0927566aa"
# GhPython's string type-hint identifier.
GHPYTHON_STRING_HINT_GUID = "37261734-EEC7-4F50-B6A8-B8D1F3C4396B"
# Grasshopper's built-in group object GUID.
GROUP_GUID = "c552a431-af5b-46a9-a8a4-0fcbc27ef596"

ACCESS = {"item": 0, "list": 1, "tree": 2}


def _param_chunk(tag, index, name, nickname, access, instance_guid, source_guid=None):
    acc = ACCESS.get(access, 0)
    source_items = ""
    if source_guid:
        source_items = f'''\n            <item name="Source" index="0" type_name="gh_guid" type_code="9">{source_guid}</item>'''
    item_count = 14 if source_guid else 13
    return f"""        <chunk name="{tag}" index="{index}">
          <items count="{item_count}">
            <item name="Name" type_name="gh_string" type_code="10">{name}</item>
            <item name="NickName" type_name="gh_string" type_code="10">{nickname}</item>
            <item name="Description" type_name="gh_string" type_code="10">Script Variable {name}</item>
            <item name="Optional" type_name="gh_bool" type_code="1">true</item>
            <item name="AllowTreeAccess" type_name="gh_bool" type_code="1">true</item>
            <item name="ShowTypeHints" type_name="gh_bool" type_code="1">true</item>
            <item name="ScriptParamAccess" type_name="gh_int32" type_code="3">{acc}</item>
{source_items}
            <item name="SourceCount" type_name="gh_int32" type_code="3">0</item>
            <item name="InstanceGuid" type_name="gh_guid" type_code="9">{instance_guid}</item>
            <item name="TypeHintID" type_name="gh_guid" type_code="9">{GHPYTHON_STRING_HINT_GUID}</item>
            <item name="WireDisplay" type_name="gh_int32" type_code="3">0</item>
            <item name="ReverseData" type_name="gh_bool" type_code="1">false</item>
            <item name="SimplifyData" type_name="gh_bool" type_code="1">false</item>
          </items>
          <chunks count="1">
            <chunk name="Attributes">
              <items count="3">
                <item name="Bounds" type_name="gh_drawing_rectanglef" type_code="35">
                  <X>0</X><Y>0</Y><W>40</W><H>20</H>
                </item>
                <item name="Pivot" type_name="gh_drawing_pointf" type_code="31">
                  <X>0</X><Y>0</Y>
                </item>
                <item name="Selected" type_name="gh_bool" type_code="1">false</item>
              </items>
              <chunks count="0"/>
            </chunk>
          </chunks>
        </chunk>"""


def _floating_string_object(index, instance_guid, name, nickname, x, y):
    return f'''            <chunk name="Object" index="{index}">
              <items count="2">
                <item name="GUID" type_name="gh_guid" type_code="9">{PARAM_STRING_GUID}</item>
                <item name="Name" type_name="gh_string" type_code="10">String</item>
              </items>
              <chunks count="1">
                <chunk name="Container">
                  <items count="6">
                    <item name="Description" type_name="gh_string" type_code="10">{name}</item>
                    <item name="InstanceGuid" type_name="gh_guid" type_code="9">{instance_guid}</item>
                    <item name="Name" type_name="gh_string" type_code="10">{name}</item>
                    <item name="NickName" type_name="gh_string" type_code="10">{nickname}</item>
                    <item name="Optional" type_name="gh_bool" type_code="1">true</item>
                    <item name="SourceCount" type_name="gh_int32" type_code="3">0</item>
                  </items>
                  <chunks count="1">
                    <chunk name="Attributes">
                      <items count="2">
                        <item name="Bounds" type_name="gh_drawing_rectanglef" type_code="35">
                          <X>{x}</X><Y>{y}</Y><W>120</W><H>20</H>
                        </item>
                        <item name="Pivot" type_name="gh_drawing_pointf" type_code="31">
                          <X>{float(x) + 60.0}</X><Y>{float(y) + 10.0}</Y>
                        </item>
                      </items>
                    </chunk>
                  </chunks>
                </chunk>
              </chunks>
            </chunk>'''


def _group_object(index, nickname, object_guid, instance_guid):
    return f'''            <chunk name="Object" index="{index}">
              <items count="2">
                <item name="GUID" type_name="gh_guid" type_code="9">{GROUP_GUID}</item>
                <item name="Name" type_name="gh_string" type_code="10">Group</item>
              </items>
              <chunks count="1">
                <chunk name="Container">
                  <items count="8">
                    <item name="Border" type_name="gh_int32" type_code="3">1</item>
                    <item name="Colour" type_name="gh_drawing_color" type_code="36">
                      <ARGB>150;170;135;255</ARGB>
                    </item>
                    <item name="Description" type_name="gh_string" type_code="10">A group of Grasshopper objects</item>
                    <item name="ID" index="0" type_name="gh_guid" type_code="9">{object_guid}</item>
                    <item name="ID_Count" type_name="gh_int32" type_code="3">1</item>
                    <item name="InstanceGuid" type_name="gh_guid" type_code="9">{instance_guid}</item>
                    <item name="Name" type_name="gh_string" type_code="10">Group</item>
                    <item name="NickName" type_name="gh_string" type_code="10">{nickname}</item>
                  </items>
                  <chunks count="1">
                    <chunk name="Attributes" />
                  </chunks>
                </chunk>
              </chunks>
            </chunk>'''


def build_ghpython_ghx(script_source, inputs, outputs):
    """
    Build a minimal .ghx XML with one GhPython Script component.

    inputs:  list of (name, nickname, access) tuples
    outputs: list of (name, nickname) tuples
    Returns the XML as bytes.
    """
    doc_id = _guid()
    inst_id = _guid()
    json_param_id = _guid()
    job_param_id = _guid()
    json_group_id = _guid()
    job_group_id = _guid()
    output_group_id = _guid()

    # Legacy GhPython stores the editor contents directly in the
    # Script/CodeInput item.  Text belongs to Rhino 8's newer Python 3
    # component and is ignored by the legacy component used here.
    encoded_script = escape(script_source)

    # Build parameter chunks
    input_chunks = []
    input_ids = []
    for i, (name, nick, access) in enumerate(inputs):
        input_id = _guid()
        input_ids.append(input_id)
        source_guid = json_param_id if i == 0 else job_param_id if i == 1 else None
        input_chunks.append(_param_chunk("InputParam", i, name, nick, access, input_id, source_guid))

    output_chunks = []
    output_ids = []
    for i, (name, nick) in enumerate(outputs):
        output_id = _guid()
        output_ids.append(output_id)
        output_chunks.append(_param_chunk("OutputParam", i, name, nick, "item", output_id))

    all_param_chunks = "\n".join(input_chunks + output_chunks)
    n_inputs = len(inputs)
    n_outputs = len(outputs)
    n_param_chunks = n_inputs + n_outputs
    top_level_objects = "\n".join([
        _group_object(0, "RH_IN:json_string", json_param_id, json_group_id),
        _group_object(1, "RH_IN:job_id", job_param_id, job_group_id),
        _group_object(2, "RH_OUT:response_json", inst_id, output_group_id),
        _floating_string_object(3, json_param_id, "RH_IN:json_string", "json_string", 0, 100),
        _floating_string_object(4, job_param_id, "RH_IN:job_id", "job_id", 0, 180),
    ])
    param_id_chunks = "\n".join(
        [
            f'                            <item name="InputId" index="{i}" type_name="gh_guid" type_code="9">{guid}</item>'
            for i, guid in enumerate(input_ids)
        ]
        + [
            f'                            <item name="OutputId" index="{i}" type_name="gh_guid" type_code="9">{guid}</item>'
            for i, guid in enumerate(output_ids)
        ]
    )
    param_id_chunks = (param_id_chunks + "\n") if param_id_chunks else ""

    xml = f"""<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<Archive name="Root">
  <items count="1">
<item name="ArchiveVersion" type_name="gh_version" type_code="80">
      <Major>0</Major>
      <Minor>2</Minor>
      <Revision>2</Revision>
    </item>
  </items>
  <chunks count="1">
    <chunk name="Definition">
      <items count="2">
        <item name="plugin_version" type_name="gh_version" type_code="80">
          <Major>1</Major>
          <Minor>0</Minor>
          <Revision>7</Revision>
        </item>
        <item name="DocumentID" type_name="gh_guid" type_code="9">{doc_id}</item>
      </items>
      <chunks count="5">
        <chunk name="DocumentHeader">
          <items count="5">
            <item name="DocumentID" type_name="gh_guid" type_code="9">{doc_id}</item>
            <item name="Preview" type_name="gh_string" type_code="10">Shaded</item>
            <item name="PreviewMeshType" type_name="gh_int32" type_code="3">1</item>
            <item name="PreviewNormal" type_name="gh_drawing_color" type_code="36">
              <ARGB>100;150;0;0</ARGB>
            </item>
            <item name="PreviewSelected" type_name="gh_drawing_color" type_code="36">
              <ARGB>100;0;150;0</ARGB>
            </item>
          </items>
          <chunks count="0"/>
        </chunk>
        <chunk name="DefinitionProperties">
          <items count="3">
            <item name="Date" type_name="gh_date" type_code="8">638110362255662983</item>
            <item name="Description" type_name="gh_string" type_code="10">MyJewl Phase C.1 native definition</item>
            <item name="Name" type_name="gh_string" type_code="10">phase_c1_native_rh_in_v1.gh</item>
          </items>
          <chunks count="3">
            <chunk name="Revisions">
              <items count="1">
                <item name="RevisionCount" type_name="gh_int32" type_code="3">0</item>
              </items>
              <chunks count="0"/>
            </chunk>
            <chunk name="Projection">
              <items count="2">
                <item name="Target" type_name="gh_drawing_point" type_code="30">
                  <X>0</X><Y>0</Y>
                </item>
                <item name="Zoom" type_name="gh_single" type_code="5">1.0</item>
              </items>
              <chunks count="0"/>
            </chunk>
            <chunk name="Views">
              <items count="1">
                <item name="ViewCount" type_name="gh_int32" type_code="3">0</item>
              </items>
              <chunks count="0"/>
            </chunk>
          </chunks>
        </chunk>
        <chunk name="RcpLayout">
          <items count="1">
            <item name="GroupCount" type_name="gh_int32" type_code="3">0</item>
          </items>
          <chunks count="0"/>
        </chunk>
        <chunk name="GHALibraries">
          <items count="1">
            <item name="Count" type_name="gh_int32" type_code="3">0</item>
          </items>
          <chunks count="0"/>
        </chunk>
        <chunk name="DefinitionObjects">
          <items count="1">
            <item name="ObjectCount" type_name="gh_int32" type_code="3">6</item>
          </items>
          <chunks count="6">
{top_level_objects}
            <chunk name="Object" index="5">
              <items count="2">
                <item name="GUID" type_name="gh_guid" type_code="9">{GHPYTHON_GUID}</item>
                <item name="Name" type_name="gh_string" type_code="10">Python Script</item>
              </items>
              <chunks count="1">
                <chunk name="Container">
                  <items count="8">
                    <item name="InstanceGuid" type_name="gh_guid" type_code="9">{inst_id}</item>
                    <item name="Name" type_name="gh_string" type_code="10">Python Script</item>
                    <item name="NickName" type_name="gh_string" type_code="10">Script</item>
                    <item name="Description" type_name="gh_string" type_code="10">GhPython Script</item>
                    <item name="Exposure" type_name="gh_int32" type_code="3">2</item>
                    <item name="Obsolete" type_name="gh_bool" type_code="1">false</item>
                    <item name="Locked" type_name="gh_bool" type_code="1">false</item>
                    <item name="Hidden" type_name="gh_bool" type_code="1">false</item>
                  </items>
                  <chunks count="2">
                    <chunk name="Attributes">
                      <items count="3">
                        <item name="Bounds" type_name="gh_drawing_rectanglef" type_code="35">
                          <X>100</X><Y>100</Y><W>150</W><H>44</H>
                        </item>
                        <item name="Pivot" type_name="gh_drawing_pointf" type_code="31">
                          <X>125</X><Y>122</Y>
                        </item>
                        <item name="Selected" type_name="gh_bool" type_code="1">false</item>
                      </items>
                      <chunks count="0"/>
                    </chunk>
                    <chunk name="Script">
                      <items count="9">
                        <item name="Description" type_name="gh_string" type_code="10">GhPython Script</item>
                        <item name="HideOutput" type_name="gh_bool" type_code="1">true</item>
                        <item name="HideInput" type_name="gh_bool" type_code="1">true</item>
                        <item name="IsAdvancedMode" type_name="gh_bool" type_code="1">false</item>
                        <item name="IconDisplay" type_name="gh_int32" type_code="3">0</item>
                        <item name="Name" type_name="gh_string" type_code="10">Python Script</item>
                        <item name="NickName" type_name="gh_string" type_code="10">Script</item>
                        <item name="MarshalOutGuids" type_name="gh_bool" type_code="1">true</item>
                        <item name="CodeInput" type_name="gh_string" type_code="10">{encoded_script}</item>
                      </items>
                      <chunks count="2">
                        <chunk name="ParameterData">
                          <items count="{2 + n_inputs + n_outputs}">
                            <item name="InputCount" type_name="gh_int32" type_code="3">{n_inputs}</item>
                            <item name="OutputCount" type_name="gh_int32" type_code="3">{n_outputs}</item>
{param_id_chunks}
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


def _modern_param_chunk(tag, index, name, nickname, access, instance_guid, source_guid=None, output=False, string_hint=False):
    """Serialize a Rhino 8 Python 3 Script variable parameter."""
    acc = ACCESS.get(access, 0)
    source_items = ""
    source_count = 0
    if source_guid:
        source_items = f'''
            <item name="Source" index="0" type_name="gh_guid" type_code="9">{source_guid}</item>'''
        source_count = 1
    if output:
        allow_tree = "false"
        description = "Script output"
    else:
        allow_tree = "true"
        description = "Script input"
    names = 12
    if source_guid:
        names += 1
    if source_guid and output:
        names += 1
    return f"""                        <chunk name=\"{tag}\" index=\"{index}\">
                          <items count=\"{names}\">
                            <item name=\"AllowTreeAccess\" type_name=\"gh_bool\" type_code=\"1\">{allow_tree}</item>
                            <item name=\"Description\" type_name=\"gh_string\" type_code=\"10\">{description}</item>
                            <item name=\"InstanceGuid\" type_name=\"gh_guid\" type_code=\"9\">{instance_guid}</item>
                            <item name=\"Name\" type_name=\"gh_string\" type_code=\"10\">{name}</item>
                            <item name=\"NickName\" type_name=\"gh_string\" type_code=\"10\">{nickname}</item>
                            <item name=\"Optional\" type_name=\"gh_bool\" type_code=\"1\">{'false' if output else 'true'}</item>
                            <item name=\"ScriptParamAccess\" type_name=\"gh_int32\" type_code=\"3\">{acc}</item>
                            <item name=\"ScriptParameterVersion\" type_name=\"gh_int32\" type_code=\"3\">2</item>
                            <item name=\"ShowTypeHints\" type_name=\"gh_bool\" type_code=\"1\">true</item>
{source_items}
                            <item name=\"SourceCount\" type_name=\"gh_int32\" type_code=\"3\">{source_count}</item>
                            <item name=\"ToolTip\" type_name=\"gh_string\" type_code=\"10\"></item>
                            <item name=\"TypeHintID\" type_name=\"gh_guid\" type_code=\"9\">{GHPYTHON_STRING_HINT_GUID if string_hint else MODERN_OBJECT_HINT_GUID}</item>
                          </items>
                          <chunks count=\"2\">
                            <chunk name=\"Attributes\">
                              <items count=\"2\">
                                <item name=\"Bounds\" type_name=\"gh_drawing_rectanglef\" type_code=\"35\">
                                  <X>0</X><Y>{260 + index * 20}</Y><W>12</W><H>20</H>
                                </item>
                                <item name=\"Pivot\" type_name=\"gh_drawing_pointf\" type_code=\"31\">
                                  <X>6</X><Y>{270 + index * 20}</Y>
                                </item>
                              </items>
                            </chunk>
                            <chunk name=\"ConverterData\">
                              <items count=\"2\">
                                <item name=\"AssemblyName\" type_name=\"gh_string\" type_code=\"10\">System.Private.CoreLib</item>
                                <item name=\"TypeName\" type_name=\"gh_string\" type_code=\"10\">{'System.String' if string_hint else 'System.Object'}</item>
                              </items>
                            </chunk>
                          </chunks>
                        </chunk>"""


def _modern_standard_output_chunk(index, instance_guid):
    return f"""                        <chunk name=\"OutputParam\" index=\"{index}\">
                          <items count=\"6\">
                            <item name=\"Description\" type_name=\"gh_string\" type_code=\"10\">Standard output and error contents collected during script run</item>
                            <item name=\"InstanceGuid\" type_name=\"gh_guid\" type_code=\"9\">{instance_guid}</item>
                            <item name=\"Name\" type_name=\"gh_string\" type_code=\"10\">out</item>
                            <item name=\"NickName\" type_name=\"gh_string\" type_code=\"10\">out</item>
                            <item name=\"Optional\" type_name=\"gh_bool\" type_code=\"1\">false</item>
                            <item name=\"SourceCount\" type_name=\"gh_int32\" type_code=\"3\">0</item>
                          </items>
                          <chunks count=\"1\"><chunk name=\"Attributes\"><items count=\"2\">
                            <item name=\"Bounds\" type_name=\"gh_drawing_rectanglef\" type_code=\"35\"><X>320</X><Y>260</Y><W>26</W><H>20</H></item>
                            <item name=\"Pivot\" type_name=\"gh_drawing_pointf\" type_code=\"31\"><X>333</X><Y>270</Y></item>
                          </items></chunk></chunks>
                        </chunk>"""


def build_python3_ghx(script_source, inputs, outputs):
    """Build a Rhino 8 native Python 3 Script GHX.

    Unlike the legacy GhPython archive, RhinoCodePluginGH stores source as
    base64 in ``Script/Text`` and identifies the language in ``LanguageSpec``.
    The parameter data is a sibling of the script chunk under ``Container``.
    """
    doc_id = _guid()
    inst_id = _guid()
    json_param_id = _guid()
    job_param_id = _guid()
    json_group_id = _guid()
    job_group_id = _guid()
    output_group_id = _guid()
    standard_output_id = _guid()
    input_ids = [_guid() for _ in inputs]
    output_ids = [_guid() for _ in outputs]
    encoded_script = base64.b64encode(script_source.encode("utf-8")).decode("ascii")

    input_chunks = []
    for i, (name, nick, access) in enumerate(inputs):
        source_guid = json_param_id if i == 0 else job_param_id if i == 1 else None
        input_chunks.append(_modern_param_chunk("InputParam", i, name, nick, access, input_ids[i], source_guid))
    output_chunks = [_modern_standard_output_chunk(0, standard_output_id)]
    for i, (name, nick) in enumerate(outputs):
        output_chunks.append(_modern_param_chunk("OutputParam", i + 1, name, nick, "item", output_ids[i], output=True))

    param_id_chunks = "\n".join(
        [f'                            <item name="InputId" index="{i}" type_name="gh_guid" type_code="9">{SCRIPT_VARIABLE_PARAM_GUID}</item>'
         for i, _ in enumerate(input_ids)]
        + [f'                            <item name="OutputId" index="0" type_name="gh_guid" type_code="9">{standard_output_id}</item>']
        + [f'                            <item name="OutputId" index="{i + 1}" type_name="gh_guid" type_code="9">{SCRIPT_VARIABLE_PARAM_GUID}</item>'
           for i, _ in enumerate(output_ids)]
    )
    parameter_data = f"""                    <chunk name=\"ParameterData\">
                      <items count=\"{2 + len(input_ids) + len(output_ids) + 1}\">
                        <item name=\"InputCount\" type_name=\"gh_int32\" type_code=\"3\">{len(input_ids)}</item>
{param_id_chunks}
                        <item name=\"OutputCount\" type_name=\"gh_int32\" type_code=\"3\">{len(output_ids) + 1}</item>
                      </items>
                      <chunks count=\"{len(input_ids) + len(output_ids) + 1}\">
{chr(10).join(input_chunks + output_chunks)}
                      </chunks>
                    </chunk>"""

    top_level_objects = "\n".join([
        _group_object(0, "RH_IN:json_string", json_param_id, json_group_id),
        _group_object(1, "RH_IN:job_id", job_param_id, job_group_id),
        _group_object(2, "RH_OUT:response_json", inst_id, output_group_id),
        _floating_string_object(3, json_param_id, "RH_IN:json_string", "json_string", 0, 100),
        _floating_string_object(4, job_param_id, "RH_IN:job_id", "job_id", 0, 180),
    ])
    return f'''<?xml version="1.0" encoding="utf-8" standalone="yes"?>
<Archive name="Root">
  <items count="1"><item name="ArchiveVersion" type_name="gh_version" type_code="80"><Major>0</Major><Minor>2</Minor><Revision>2</Revision></item></items>
  <chunks count="1">
    <chunk name="Definition">
      <items count="2">
        <item name="plugin_version" type_name="gh_version" type_code="80"><Major>1</Major><Minor>0</Minor><Revision>7</Revision></item>
        <item name="DocumentID" type_name="gh_guid" type_code="9">{doc_id}</item>
      </items>
      <chunks count="5">
        <chunk name="DocumentHeader"><items count="5">
          <item name="DocumentID" type_name="gh_guid" type_code="9">{doc_id}</item>
          <item name="Preview" type_name="gh_string" type_code="10">Shaded</item>
          <item name="PreviewMeshType" type_name="gh_int32" type_code="3">1</item>
          <item name="PreviewNormal" type_name="gh_drawing_color" type_code="36"><ARGB>100;150;0;0</ARGB></item>
          <item name="PreviewSelected" type_name="gh_drawing_color" type_code="36"><ARGB>100;0;150;0</ARGB></item>
        </items><chunks count="0"/></chunk>
        <chunk name="DefinitionProperties"><items count="3">
          <item name="Date" type_name="gh_date" type_code="8">638110362255662983</item>
          <item name="Description" type_name="gh_string" type_code="10">MyJewl Phase C.1 native definition</item>
          <item name="Name" type_name="gh_string" type_code="10">phase_c1_native_rh_in_v1.gh</item>
        </items><chunks count="3"><chunk name="Revisions"><items count="1"><item name="RevisionCount" type_name="gh_int32" type_code="3">0</item></items></chunk><chunk name="Projection"><items count="2"><item name="Target" type_name="gh_drawing_point" type_code="30"><X>0</X><Y>0</Y></item><item name="Zoom" type_name="gh_single" type_code="5">1.0</item></items></chunk><chunk name="Views"><items count="1"><item name="ViewCount" type_name="gh_int32" type_code="3">0</item></items></chunk></chunks></chunk>
        <chunk name="RcpLayout"><items count="1"><item name="GroupCount" type_name="gh_int32" type_code="3">0</item></items><chunks count="0"/></chunk>
        <chunk name="GHALibraries"><items count="1"><item name="Count" type_name="gh_int32" type_code="3">0</item></items><chunks count="0"/></chunk>
        <chunk name="DefinitionObjects">
          <items count="1"><item name="ObjectCount" type_name="gh_int32" type_code="3">6</item></items>
          <chunks count="6">
{top_level_objects}
            <chunk name="Object" index="5">
              <items count="2"><item name="GUID" type_name="gh_guid" type_code="9">{PYTHON3_GUID}</item><item name="Name" type_name="gh_string" type_code="10">Python 3 Script</item></items>
              <chunks count="1"><chunk name="Container">
                <items count="14">
                  <item name="Description" type_name="gh_string" type_code="10">Python 3 scripting component</item>
                  <item name="GraftStandardOutputLines" type_name="gh_bool" type_code="1">true</item>
                  <item name="InstanceGuid" type_name="gh_guid" type_code="9">{inst_id}</item>
                  <item name="MarshGuids" type_name="gh_bool" type_code="1">true</item>
                  <item name="MarshInputs" type_name="gh_bool" type_code="1">true</item>
                  <item name="MarshOutputs" type_name="gh_bool" type_code="1">true</item>
                  <item name="Name" type_name="gh_string" type_code="10">Python 3 Script</item>
                  <item name="NickName" type_name="gh_string" type_code="10">Python 3 Script</item>
                  <item name="ScriptComponentVersion" type_name="gh_int32" type_code="3">3</item>
                  <item name="Tooltip" type_name="gh_string" type_code="10"></item>
                  <item name="UsingLibraryInputParam" type_name="gh_bool" type_code="1">false</item>
                  <item name="UsingScriptInputParam" type_name="gh_bool" type_code="1">false</item>
                  <item name="UsingScriptOutputParam" type_name="gh_bool" type_code="1">false</item>
                  <item name="UsingStandardOutputParam" type_name="gh_bool" type_code="1">true</item>
                </items>
                <chunks count="4">
                  <chunk name="Attributes"><items count="2"><item name="Bounds" type_name="gh_drawing_rectanglef" type_code="35"><X>100</X><Y>100</Y><W>150</W><H>44</H></item><item name="Pivot" type_name="gh_drawing_pointf" type_code="31"><X>125</X><Y>122</Y></item></items></chunk>
{parameter_data}
                  <chunk name="Script"><items count="5">
                    <item name="MarshGuids" type_name="gh_bool" type_code="1">true</item>
                    <item name="MarshInputs" type_name="gh_bool" type_code="1">true</item>
                    <item name="MarshOutputs" type_name="gh_bool" type_code="1">true</item>
                    <item name="Text" type_name="gh_string" type_code="10">{encoded_script}</item>
                    <item name="Title" type_name="gh_string" type_code="10">Python 3 Script</item>
                  </items><chunks count="1"><chunk name="LanguageSpec"><items count="2"><item name="Taxon" type_name="gh_string" type_code="10">*.*.python</item><item name="Version" type_name="gh_string" type_code="10">3.*</item></items><chunks count="0"/></chunk></chunks></chunk>
                  <chunk name="ScriptEditor"><items count="1"><item name="StartBounds" type_name="gh_drawing_rectangle" type_code="34"><X>449</X><Y>510</Y><W>550</W><H>650</H></item></items><chunks count="0"/></chunk>
                </chunks>
              </chunk></chunks>
            </chunk>
          </chunks>
        </chunk>
      </chunks>
    </chunk>
  </chunks>
</Archive>'''.encode("utf-8")


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
