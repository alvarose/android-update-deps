package com.example.fixture
import android.app.Activity
import android.os.Bundle
import com.google.gson.Gson
class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        println(Gson().toJson(mapOf("hello" to "world")))
    }
}
