using UnityEngine;

public class GrassBlades : MonoBehaviour {
    public GameObject grassBladePrefab;
    public LayerMask groundLayer;
    public float grassHeight = 1.0f;
    public float numPrefabs = 10.0f;
    public Material grassMaterial;

    void Start() {
        // Get the mesh renderer for the object this script is attached to
        MeshRenderer renderer = GetComponent<MeshRenderer>();
        if (renderer == null) {
            Debug.LogError("GrassBlades script requires a MeshRenderer component!");
            return;
        }

        // Get the mesh filter for the object this script is attached to
        MeshFilter filter = GetComponent<MeshFilter>();
        if (filter == null) {
            Debug.LogError("GrassBlades script requires a MeshFilter component!");
            return;
        }

        // Create a new mesh with the same vertices as the original mesh
        Mesh mesh = filter.mesh;
        Vector3[] vertices = mesh.vertices;
        Vector3[] normals = mesh.normals;

        // Set the material of the grass blades to an unlit material
        Material unlitMaterial = new Material(grassMaterial);
        unlitMaterial.shader = Shader.Find("Unlit/Color");

        // Create grass blades for each vertex on the mesh
        for (int i = 0; i < vertices.Length; i++) {
            // Find the position of the vertex in world space
            Vector3 worldPos = transform.TransformPoint(vertices[i]);

            // Cast a ray down to the ground to determine the surface color
            RaycastHit hit;
            if (Physics.Raycast(worldPos, Vector3.down, out hit, Mathf.Infinity, groundLayer)) {
                // Create multiple grass blades at each vertex
                for (int j = 0; j < numPrefabs; j++) {
                    float angle = Random.Range(0.0f, Mathf.PI * 2.0f);
                    Vector3 offset = new Vector3(Mathf.Cos(angle), 0.0f, Mathf.Sin(angle)) * Random.Range(0.0f, 0.2f);
                    Vector3 bladePos = worldPos + offset;

                    // Instantiate the grass blade prefab
                    GameObject grassBlade = Instantiate(grassBladePrefab, bladePos, Quaternion.identity);
                    grassBlade.transform.up = normals[i];
                    grassBlade.transform.localScale = new Vector3(1, grassHeight, 1);

                    // Set the material of the grass blade to an unlit material
                    MeshRenderer bladeRenderer = grassBlade.GetComponent<MeshRenderer>();
                    if (bladeRenderer != null) {
                        bladeRenderer.material = unlitMaterial;
                    }

                    // Set the color of the grass blade to the color of the ground beneath it
                    if (bladeRenderer != null) {
                        bladeRenderer.material.color = hit.collider.gameObject.GetComponent<MeshRenderer>().material.color;
                    }
                }
            }
        }
    }
}
