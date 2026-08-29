Shader "Custom/StylizedTreeShader"
{
    Properties
    {
        [Header(Main Properties)]


        _Color ("Color", Color) = (131,245,75,100)
        _MainTex ("Main Texture", 2D) = "white" {}
        _LightCutoff("Light cutoff", Range(0,1)) = 0.5
        _TextureCutoff("Texture Alpha Cutoff", Range(0,1)) = 0.5
        _RimSize("Rim size", Range(0,1)) = 0.5
        [HDR]_RimColor("Rim color", Color) = (199,245,75,76)
        [Toggle(SHADOWED_RIM)]
        _ShadowedRim("Rim shadow scattering", float) = 0
        [HDR]_Emission("HDR Emission", Color) = (0,0,0,1)

        [Header(Leaf Displacement)]
        _DisplacementGuide("Displacement Texture", 2D) = "white" {}
        _DisplacementAmount("Displacement amount", float) = 0
        _DisplacementSpeed("Displacement speed", float) = 0

        [Header(Sub Surface Scattering)]
        _SSSConcentration("Sub Surface Scattering Area", float) = 0
        [HDR]_SSSColor("Sub Surface Scattering color", Color) = (255,255,255,76)
    }
    SubShader
    {
        Tags { "RenderType"="Opaque" }
        Cull Off
        LOD 200

        CGPROGRAM
        #pragma surface surf Tree fullforwardshadows addshadow
        #pragma shader_feature SHADOWED_RIM
        #pragma target 3.0


        fixed4 _Color;
        sampler2D _MainTex;
        float _LightCutoff;
        float _TextureCutoff;
        float _RimSize;
        fixed4 _RimColor;
        fixed4 _Emission;
        float _SSSConcentration;
        fixed4 _SSSColor;
        sampler2D _DisplacementGuide;
        float _DisplacementAmount;
        float _DisplacementSpeed;

        struct Input
        {
            float2 uv_MainTex;
            float2 uv_DisplacementGuide;
        };

        struct SurfaceOutputTree
        {
            fixed3 Albedo;
            fixed3 Normal;
            float Smoothness;
            half3 Emission;
            float4 SSS;
            fixed Alpha;
        };
/// Main Lighting and the Light Dir, Atten is calculated here using nDOTl and the Light Dir is Calculated and
/// Normalized
        half4 LightingTree (SurfaceOutputTree s, half3 lightDir, half3 viewDir, half atten) {
            half nDotL = saturate(dot(s.Normal, normalize(lightDir)));
            half diff = step(_LightCutoff, nDotL);
            half sssAmount = pow(saturate(dot(normalize(viewDir), -normalize(lightDir))), _SSSConcentration);
            fixed4 sssColor = sssAmount * s.SSS;
            float rimArea = step(1 - _RimSize ,1 - saturate(dot(normalize(viewDir), s.Normal)));
            float3 rim = _RimColor * rimArea;
            half stepAtten = round(atten);
            half shadow = diff * stepAtten;
            half3 col = s.Albedo * _LightColor0;
            half4 c;
            #ifdef SHADOWED_RIM
            c.rgb = (col + rim) * shadow;
            #else
            c.rgb = col * shadow + rim;
            #endif
            c.rgb += sssColor.rgb * stepAtten * diff;
            c.a = s.Alpha;
            return c;
        }

        UNITY_INSTANCING_BUFFER_START(Props)
            // put more per-instance properties here
        UNITY_INSTANCING_BUFFER_END(Props)

        void surf (Input IN, inout SurfaceOutputTree o)
        {
            float2 displ = (tex2D(_DisplacementGuide, IN.uv_DisplacementGuide + _Time.y * _DisplacementSpeed).xy * 2.0 - 1.0) * _DisplacementAmount;
            fixed4 tex = tex2D(_MainTex, IN.uv_MainTex + displ);
            fixed4 c = _Color;

            o.Albedo = c.rgb;
            clip(tex.a - _TextureCutoff);
            o.Emission = o.Albedo * _Emission;
            o.SSS = _SSSColor;
            o.Alpha = c.a;
        }
        ENDCG
    }
    FallBack "Diffuse"
}
