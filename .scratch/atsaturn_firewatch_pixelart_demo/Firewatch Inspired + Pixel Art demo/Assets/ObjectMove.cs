using UnityEngine;

public class ObjectMove : MonoBehaviour {
    
    public float speed = 5f; // Movement speed of the game object

    void Update () {
        // Move the game object forward
        transform.Translate(Vector3.forward * speed * Time.deltaTime);
    }
}
