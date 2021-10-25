bl_info = {
    "name": "Export Nori scenes format",
    "author": "Adrien Gruson, Philipp Lindenberger",
    "version": (0, 2),
    "blender": (2, 80, 0),
    "location": "File > Export > Nori exporter (.xml)",
    "description": "Export Nori scenes format (.xml)",
    "warning": "",
    "wiki_url": "",
    "support": "TESTING",
    "category": "Export"}

import bpy, os, math, shutil
from mathutils import Matrix, Vector, Color
from xml.dom.minidom import Document
import io_scene_obj.export_obj
from io_scene_obj.export_obj import name_compat
from bpy_extras import io_utils, node_shader_utils
import bmesh
from bpy_extras.wm_utils.progress_report import (
    ProgressReport,
    ProgressReportSubstep,
)

# -----------------------------------------------------------------------------
# Module-level Shared State

watched_objects = {}  # used to trigger compositor updates on scene updates

SUPPORTED_OBJECT_TYPES = {"MESH", "CURVE", "FONT", "META", "EMPTY", "SURFACE"} #Formats we can save as .obj

# Overwite an objects material with another objects materials
def copy_materials(ob_src, ob_target):
    ob_target.data.materials.clear()
    for mat in ob_src.data.materials:
        ob_target.data.materials.append(mat)
    ob_target.active_material = ob_src.active_material

# Function to temporarily join all instances of an object into a single mesh for exports
def join_instances(context, ob):
    mwi = ob.matrix_world.inverted()
    dg = context.evaluated_depsgraph_get()

    bm = bmesh.new()

    is_instance = False
    for ob_inst in dg.object_instances:
        if ob_inst.parent and ob_inst.object.original == ob:
            is_instance = True
            me = ob_inst.instance_object.to_mesh()
            bm.from_mesh(me)
            # transform to match instance
            bmesh.ops.transform(bm,
                    matrix=mwi @ ob_inst.matrix_world,
                    verts=bm.verts[-len(me.vertices):]
                    )

    # link an object with the instanced mesh
    if (not is_instance):
        return ob, is_instance
    me = bpy.data.meshes.new(f"{ob.data.name}_InstanceMesh")
    bm.to_mesh(me)
    ob_ev = bpy.data.objects.new(f"{ob.name}_InstancedObject", me) 
    ob_ev.matrix_world = ob.matrix_world

    copy_materials(ob, ob_ev)

    context.collection.objects.link(ob_ev)
    return ob_ev, is_instance

# Main class exporter
class NoriWriter:
    def verbose(self,text):
        print(text)

    def __init__(self, context, filepath):
        self.context = context
        self.depsgraph = context.evaluated_depsgraph_get()
        self.scene = context.scene
        self.filepath = filepath
        self.workingDir = os.path.dirname(self.filepath)
        self.export_textures = False

    ######################
    # tools private methods
    # (xml format)
    ######################
    def __createElement(self, name, attr):
        el = self.doc.createElement(name)
        for k,v in attr.items():
            el.setAttribute(k,v)
        return el

    def __createEntry(self, t, name, value):
        return self.__createElement(t,{"name":name,"value":value})

    def __createVector(self, t, vec):
        return self.__createElement(t, {"value": "%f %f %f" % (vec[0],vec[1],vec[2])})

    def __createColorOrTexture(self, name, color_socket):
        c = color_socket.default_value
        linked_nodes = color_socket.links
        color_entry = self.__createEntry("color", name,"%f,%f,%f" %(c[0],c[1],c[2]))
        try:
            if len(linked_nodes)> 0 and self.export_textures:
                if (linked_nodes[0].from_node.bl_label == "Image Texture"):
                    texture = self.__createElement("texture",{"type":"image_texture", "name":name})
                    texture.appendChild(self.__createEntry("string","filename", linked_nodes[0].from_node.image.filepath.replace("\\","/")))
                    texture.appendChild(self.__createEntry("string","interpolation", linked_nodes[0].from_node.interpolation))
                    texture.appendChild(self.__createEntry("string","extension", linked_nodes[0].from_node.extension))
                    texture.appendChild(self.__createEntry("string","projection", linked_nodes[0].from_node.projection))
                    color_entry = texture
        except:
            # To be safe
            self.verbose("No Suitable texture found. Return default color.")
        return color_entry

    def __createTransform(self, mat, el = None, export_meshes_world = False):
        if (export_meshes_world):
            mat = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0],[0.0, 0.0, 0.0, 1.0]]
        transform = self.__createElement("transform",{"name":"toWorld"})
        if(el):
            transform.appendChild(el)
        value = ""
        for j in range(4):
            for i in range(4):
                value += str(mat[j][i])+","
        transform.appendChild(self.__createElement("matrix",{"value":value[:-1]}))
        return transform

    def setExportMeshesWorld(self, export_meshes_world):
        self.export_meshes_world = export_meshes_world

    def write(self, exportLight, exportMaterialColor, nbSamples):
        """Main method to write the blender scene into Nori format
        It will export as follows:
         1) write integrator configuration
         2) write samples information (number, distribution)
         3) export one camera
         4) export all light sources
         5) export all meshes (+bsdf) (+ area emitter)"""

        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode='OBJECT')

        # create xml document
        self.doc = Document()
        self.scene = self.doc.createElement("scene")
        self.doc.appendChild(self.scene)
        
        ######################
        # 1) write integrator configuration
        ######################
        if(not exportLight):
            self.scene.appendChild(self.__createElement("integrator", {"type" : "normals" }))
        else:
            self.scene.appendChild(self.__createElement("integrator", {"type" : "path_mis" }))

        ######################
        # 2) write the number of samples
        # and which distribution we will use
        ######################
        sampler = self.__createElement("sampler", {"type" : "independent" })
        sampler.appendChild(self.__createElement("integer", {"name":"sampleCount", "value":str(nbSamples)}))
        self.scene.appendChild(sampler)

        ######################
        # 3) export one camera
        ######################
        # note that we only support one camera
        cameras = [cam for cam in self.context.scene.objects
                       if cam.type in {'CAMERA'}]
        if(len(cameras) == 0):
            self.verbose("WARN: No camera to export")
        else:
            if(len(cameras) > 1):
                self.verbose("WARN: Does not handle multiple camera, only export the first one")
            self.scene.appendChild(self.write_camera(cameras[0])) # export the first one
        ######################
        # 4) export all light sources
        ######################
        if(exportLight):
            sources = [obj for obj in self.context.scene.objects
                          if obj.type in {'LIGHT'} and obj.visible_get()]
            for source in sources:
                if(source.data.type == "POINT"):
                    pointLight = self.__createElement("emitter", {"type" : "point" })
                    pos = source.location
                    pointLight.appendChild(self.__createEntry("point", "position", "%f,%f,%f"%(pos.x,pos.y,pos.z)))
                    power = source.data.energy
                    color = list(source.data.color).copy()
                    color[0] *=power
                    color[1] *=power
                    color[2] *=power
                    pointLight.appendChild(self.__createEntry("color", "power", "%f,%f,%f"%(color[0], color[1], color[2])))
                    self.scene.appendChild(pointLight)
                else:
                    self.verbose("WARN: Light source type (%s) is not supported" % source.data.type)

        ######################
        # 5) export all meshes
        ######################
        # create the directory for store the meshes
        if not os.path.exists(self.workingDir+"/meshes"):
                os.makedirs(self.workingDir+"/meshes")

        #io_scene_obj.export_obj.save(self.context, self.workingDir+"/all.obj")

        # export all of them
        meshes = [obj for obj in self.context.scene.objects
                      if obj.visible_get() and obj.type in SUPPORTED_OBJECT_TYPES]
        
        with ProgressReport(self.context.window_manager) as progress:
            progress.enter_substeps(len(meshes))
            for mesh in meshes:
                self.write_mesh(mesh, exportLight, exportMaterialColor, progress)
            progress.leave_substeps()
        ######################
        # 6) write the xml file
        ######################
        self.doc.writexml(open(self.filepath, "w"), "", "\t","\n")

    def write_camera(self, cam):
        """convert the selected camera (cam) into xml format"""
        camera = self.__createElement("camera",{"type":"perspective"})
        camera.appendChild(self.__createEntry("float","fov",str(cam.data.angle*180/math.pi)))
        camera.appendChild(self.__createEntry("float","nearClip",str(cam.data.clip_start)))
        camera.appendChild(self.__createEntry("float","farClip",str(cam.data.clip_end)))
        percent = self.context.scene.render.resolution_percentage/100.0
        camera.appendChild(self.__createEntry("integer","width",str(int(self.context.scene.render.resolution_x*percent))))
        camera.appendChild(self.__createEntry("integer","height",str(int(self.context.scene.render.resolution_y*percent))))
        trans = self.__createTransform(cam.matrix_world, self.__createVector("scale",(1,1,-1)))
        camera.appendChild(trans)
        return camera

    ######################
    # meshes related methods
    ######################
    def __createMeshEntry(self, filename, matrix):
        meshElement = self.__createElement("mesh", {"type" : "obj"})
        meshElement.appendChild(self.__createElement("string", {"name":"filename","value":"meshes/"+filename}))
        if not self.export_meshes_world:
            meshElement.appendChild(self.__createTransform(matrix))
        return meshElement

    def __createBSDFEntry(self, slot, exportMaterialColor):
        """method responsible to the auto-conversion
        between Blender internal BSDF (not Cycles!) and Nori BSDF
        """

        node_tree = slot.material.node_tree

        if (node_tree is None):
            c = slot.material.diffuse_color
            bsdfElement = self.__createElement("bsdf", {"type":"diffuse", "name" : slot.material.name})
            bsdfElement.appendChild(self.__createEntry("color", "albedo","%f,%f,%f" %(c[0],c[1],c[2])))
            return bsdfElement
        nodes = node_tree.nodes

        diffuse = nodes.get("Diffuse BSDF")
        principled = nodes.get("Principled BSDF")
        specular = nodes.get("Specular")
        glass = nodes.get("Glass BSDF")
        glossy = nodes.get("Glossy BSDF")

        if (glass and exportMaterialColor):
            ior = glass.inputs["IOR"].default_value
            bsdfElement = self.__createElement("bsdf", {"type":"dielectric", "name" : slot.material.name}) # For compatibility reasons this is not called roughdielectric
            bsdfElement.appendChild(self.__createColorOrTexture("color", glass.inputs["Color"]))
            bsdfElement.appendChild(self.__createEntry("float", "IOR","%f" % ior))
            bsdfElement.appendChild(self.__createEntry("float", "roughness","%f" % glass.inputs["Roughness"].default_value))
        elif (glossy and exportMaterialColor):
            alpha = glossy.inputs["Roughness"].default_value
            bsdfElement = self.__createElement("bsdf", {"type":"microfacet", "name" : slot.material.name})
            bsdfElement.appendChild(self.__createColorOrTexture("kd", glossy.inputs["Color"]))
            bsdfElement.appendChild(self.__createEntry("float", "alpha","%f" % alpha))
        elif (diffuse and exportMaterialColor):
            bsdfElement = self.__createElement("bsdf", {"type":"diffuse", "name" : slot.material.name})
            bsdfElement.appendChild(self.__createColorOrTexture("albedo", diffuse.inputs["Color"]))

        elif (principled and exportMaterialColor):
            c = principled.inputs["Base Color"].default_value
            bsdfElement = self.__createElement("bsdf", {"type":"disney", "name" : slot.material.name})
            bsdfElement.appendChild(self.__createColorOrTexture("baseColor", principled.inputs["Base Color"]))
            bsdfElement.appendChild(self.__createEntry("float", "metallic","%f" %(principled.inputs["Metallic"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "subsurface","%f" %(principled.inputs["Subsurface"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "specular","%f" %(principled.inputs["Specular"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "specularTint","%f" %(principled.inputs["Specular Tint"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "roughness","%f" %(principled.inputs["Roughness"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "anisotropic","%f" %(principled.inputs["Anisotropic"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "sheen","%f" %(principled.inputs["Sheen"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "sheenTint","%f" %(principled.inputs["Sheen Tint"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "clearcoat","%f" %(principled.inputs["Clearcoat"].default_value)))
            bsdfElement.appendChild(self.__createEntry("float", "clearcoatGloss","%f" %(principled.inputs["Clearcoat Roughness"].default_value)))


        elif (specular and exportMaterialColor):
            bsdfElement = self.__createElement("bsdf", {"type":"mirror", "name" : slot.material.name})
        else:
            c = slot.material.diffuse_color
            bsdfElement = self.__createElement("bsdf", {"type":"diffuse", "name" : slot.material.name})
            bsdfElement.appendChild(self.__createEntry("color", "albedo","%f,%f,%f" %(c[0],c[1],c[2])))

        return bsdfElement

    def write_mesh(self,mesh, exportMeshLights, exportMaterialColor, progress):
        if mesh.type in SUPPORTED_OBJECT_TYPES and mesh.type != "EMPTY":
            for meshEntry in self.write_mesh_objs(mesh, exportMeshLights, exportMaterialColor, progress):
                self.scene.appendChild(meshEntry)

    def write_mesh_objs(self, mesh, exportMeshLights, exportMaterialColor, progress):

        # We check if the object has any other instances, and if so create a temporary joined object.
        mesh, created_instance_ob = join_instances(self.context, mesh)
        mesh = mesh.evaluated_get(self.depsgraph) #this gives us the evaluated version of the object. 
        #Aka with all modifiers and deformations applied.

        haveMaterial = (len(mesh.material_slots) != 0 and mesh.material_slots[0].name != '')

        # export obj file base (vertex pos, normal and uv)
        # but not the face data
        fileObjPath = name_compat(mesh.name)+".obj"

        # write_file export by default meshes in world coordinates, we transform back to local coordinate.
        world_to_local = mesh.matrix_world.copy().inverted_safe()

        # We use the official .obj export scripts, modified to our needs
        progress.enter_substeps(1)
        io_scene_obj.export_obj.write_file(self.workingDir+"/meshes/"+fileObjPath, [mesh], self.depsgraph, self.scene, 
                    progress=progress,
                    EXPORT_NORMALS=True,
                    EXPORT_UV=True,
                    EXPORT_TRI=self.export_triangular,
                    EXPORT_GROUP_BY_MAT=True,
                    EXPORT_GROUP_BY_OB = False,
                    EXPORT_APPLY_MODIFIERS=True,
                    EXPORT_CURVE_AS_NURBS=True,
                    EXPORT_MTL=True,
                    EXPORT_KEEP_VERT_ORDER=False,
                    EXPORT_GLOBAL_MATRIX=world_to_local if not self.export_meshes_world else None)
        progress.leave_substeps()
        # if added_uv:
        #     mesh.data.uv_layers.remove(mesh.data.uv_layers['DefaultUvMap'])
        #     dg = bpy.context.evaluated_depsgraph_get()
        #     mesh = mesh.evaluated_get(dg)
        # write all polygones (faces)
        listMeshXML = []
        if(not haveMaterial):
            # add default BSDF
            meshElement = self.__createMeshEntry(fileObjPath, mesh.matrix_world)
            bsdfElement = self.__createElement("bsdf", {"type":"diffuse"})
            bsdfElement.appendChild(self.__createEntry("color", "albedo", "0.75,0.75,0.75"))
            meshElement.appendChild(bsdfElement)
            listMeshXML = [meshElement]
        else:
            for id_mat in range(len(mesh.material_slots)):
                slot = mesh.material_slots[id_mat]
                self.verbose("MESH: "+mesh.name+" BSDF: "+slot.name)

                # we create an new obj file and concatenate data files
                fileObjMatPath = name_compat(mesh.name)+"_"+name_compat(slot.name)+".obj"
                fileObj = open(self.workingDir+"/meshes/"+fileObjPath,"r")
                fileObjMat = open(self.workingDir+"/meshes/"+fileObjMatPath,"w")
                
                # We only take faces that are associated to this material
                do_copy = True
                for line in fileObj:
                    if (line.startswith("g")):
                        if not line.startswith("g " + name_compat(mesh.name)+"_"+name_compat(mesh.data.name)+"_"+name_compat(slot.name)):
                            do_copy=False
                        else:
                            do_copy=True
                    if (do_copy):
                        fileObjMat.write(line)

                # We create xml related entry
                meshElement = self.__createMeshEntry(fileObjMatPath, mesh.matrix_world)
                meshElement.appendChild(self.__createBSDFEntry(slot, exportMaterialColor))

                fileObjMat.close()
                fileObj.close()
                # Check for emissive surfaces
                node_tree = slot.material.node_tree

                if (node_tree is None):
                    continue
                nodes = node_tree.nodes
                emission = nodes.get("Emission")
                
                if (emission and exportMeshLights):
                    strength = emission.inputs["Strength"].default_value
                    color = emission.inputs["Color"].default_value
                    vec = [0,0,0]
                    vec[0] = color[0] * strength
                    vec[1] = color[1] * strength 
                    vec[2] = color[2] * strength 

                    areaLight = self.__createElement("emitter", {"type" : "area" })
                    areaLight.appendChild(self.__createEntry("color", "radiance", "%f,%f,%f"%(vec[0],vec[1],vec[2])))
                    meshElement.appendChild(areaLight)

                listMeshXML.append(meshElement)

            # Clean temporal obj file: outcommented since the combined and grouped output might be usable
            if len(mesh.material_slots) <= 1:
               os.remove(self.workingDir+"/meshes/"+fileObjPath)

        # free the memory
        # bpy.data.meshes.remove(mesh.data)
        if (created_instance_ob):
            bpy.data.objects.remove(mesh, do_unlink=True)


        return listMeshXML

######################
# blender code
######################
from bpy.props import StringProperty, IntProperty, BoolProperty
from bpy_extras.io_utils import ExportHelper

class NoriExporter(bpy.types.Operator, ExportHelper):
    """Export a blender scene into Nori scene format"""

    # add to menu
    bl_idname = "export.nori"
    bl_label = "Export Nori scene"

    # filtering file names
    filename_ext = ".xml"
    filter_glob = StringProperty(default="*.xml", options={'HIDDEN'})

    ###################
    # other options
    ###################

    export_light = BoolProperty(
                    name="Export Lights",
                    description="Export light to Nori",
                    default=True)

    export_material_colors = BoolProperty(
                    name="Export BSDF properties",
                    description="Export material colors instead of viewport colors",
                    default=False)
    
    export_textures = BoolProperty(
                    name="Export Textures",
                    description="Export texture connected to color socket of the material. Only effective \
                     when 'Export BSDF properties' is selected.",
                    default=False)

    export_meshes_in_world = BoolProperty(
                    name="Export OBJ in world coords",
                    description="Export meshes in world coordinate frame.",
                    default=False)
    
    export_meshes_triangular = BoolProperty(
                    name="Triangular Mesh",
                    description="Convert faces to triangles.",
                    default=False)

    nb_samples = IntProperty(name="Numbers of camera rays",
                    description="Number of camera ray",
                    default=32)

    
    

    def execute(self, context):
        nori = NoriWriter(context, self.filepath)
        nori.setExportMeshesWorld(self.export_meshes_in_world)
        nori.export_triangular = self.export_meshes_triangular
        nori.export_textures = self.export_textures
        nori.write(self.export_light, self.export_material_colors, self.nb_samples)
        return {'FINISHED'}

    def invoke(self, context, event):
        #self.frame_start = context.scene.frame_start
        #self.frame_end = context.scene.frame_end

        wm = context.window_manager
        wm.fileselect_add(self)
        return {'RUNNING_MODAL'}


def menu_export(self, context):
    import os
    default_path = os.path.splitext(bpy.data.filepath)[0] + ".xml"
    self.layout.operator(NoriExporter.bl_idname, text="Export Nori scenes...").filepath = default_path


# Register Nori exporter inside blender
def register():
    bpy.utils.register_class(NoriExporter)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)

def unregister():
    bpy.utils.unregister_class(NoriExporter)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)

if __name__ == "__main__":
    register()