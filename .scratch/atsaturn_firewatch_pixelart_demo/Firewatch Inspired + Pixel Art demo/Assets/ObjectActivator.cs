using UnityEngine;
using System.Collections;

public class ObjectActivator : MonoBehaviour {

    public float delayTime = 2f; // Time between object activation
    public GameObject[] objects; // Array of objects to activate

    private int currentIndex = 0;
    private float timer = 0f;

    void Start () {
        // Activate the first object
        objects[currentIndex].SetActive(true);
    }

    void Update () {
        // Increment timer
        timer += Time.deltaTime;

        // Check if delay time has elapsed
        if (timer >= delayTime) {
            // Turn off the current object
            objects[currentIndex].SetActive(false);

            // Increment the index, and loop back to the beginning if necessary
            currentIndex = (currentIndex + 1) % objects.Length;

            // Activate the next object
            objects[currentIndex].SetActive(true);

            // Reset timer
            timer = 0f;
        }
    }
}
