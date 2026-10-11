using System.Collections;
using System.Collections.Generic;
using UnityEngine;

public class GrassSpawner : MonoBehaviour
{
    public Sprite grassSprite; // The sprite to use for the grass
    public int numGrassSprites = 100; // The number of grass sprites to spawn
    public float minScale = 0.5f; // The minimum scale of the grass sprites
    public float maxScale = 1.5f; // The maximum scale of the grass sprites
    public float yOffset = 0.1f; // The y offset of the grass sprites from the mesh surface
    public float maxXRotation = 10f; // The maximum rotation around the x-axis

    void Start()
    {
        // Get the mesh renderer component for the object this script is attached to
        MeshRenderer meshRenderer = GetComponent<MeshRenderer>();

        // If there is no mesh renderer, exit the script
        if (meshRenderer == null)
        {
            Debug.LogError("GrassSpawner script requires a MeshRenderer component on the same GameObject.");
            return;
        }

        // Get the bounds of the mesh
        Bounds bounds = meshRenderer.bounds;

        // Get the color of the mesh at the center point
        Vector3 centerPoint = bounds.center;
        Color meshColor = meshRenderer.material.color;

        // Loop through and spawn the grass sprites
        for (int i = 0; i < numGrassSprites; i++)
        {
            // Get a random point within the bounds of the mesh
            Vector3 randomPoint = new Vector3(Random.Range(bounds.min.x, bounds.max.x),
                                              Random.Range(bounds.min.y, bounds.max.y),
                                              Random.Range(bounds.min.z, bounds.max.z));

            // Get the normal of the mesh at the random point
            Vector3 normal = meshRenderer.transform.TransformDirection(meshRenderer.GetComponent<MeshFilter>().mesh.normals[0]);

            // Calculate the position of the grass sprite based on the random point and the mesh normal
            Vector3 position = randomPoint + normal * yOffset;

            // Instantiate the grass sprite and set its position and rotation
            GameObject grassObject = new GameObject("GrassSprite");
            SpriteRenderer spriteRenderer = grassObject.AddComponent<SpriteRenderer>();
            spriteRenderer.sprite = grassSprite;
            spriteRenderer.sortingLayerName = "Grass"; // Make sure to create the "Grass" sorting layer in Unity's editor
            grassObject.transform.position = position;

            // Randomize the scale and x rotation of the grass sprite
            float scale = Random.Range(minScale, maxScale);
            float xRotation = Random.Range(-maxXRotation, maxXRotation);
            grassObject.transform.localScale = new Vector3(scale, scale, 1f);
            grassObject.transform.rotation = Quaternion.Euler(0f, Random.Range(0f, 360f), 0f) * Quaternion.FromToRotation(Vector3.up, normal);

            // Set the color of the grass sprite to match the mesh color
            spriteRenderer.color = meshColor;
        }
    }
}
