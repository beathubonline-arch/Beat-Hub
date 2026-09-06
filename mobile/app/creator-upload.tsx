import { useState } from 'react';
import { router } from 'expo-router';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { ActivityIndicator, Image, Pressable, SafeAreaView, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { api, getAccessToken } from '../src/api';

export default function CreatorUpload() {
  const [title,setTitle]=useState(''); const [description,setDescription]=useState('');
  const [genre,setGenre]=useState(''); const [bpm,setBpm]=useState(''); const [tags,setTags]=useState('');
  const [price,setPrice]=useState(''); const [currency,setCurrency]=useState<'KES'|'USD'>('KES');
  const [salesModel,setSalesModel]=useState<'non_exclusive'|'exclusive'>('non_exclusive');
  const [audio,setAudio]=useState<DocumentPicker.DocumentPickerAsset|null>(null);
  const [cover,setCover]=useState<ImagePicker.ImagePickerAsset|null>(null);
  const [busy,setBusy]=useState(false); const [error,setError]=useState(''); const [success,setSuccess]=useState('');

  async function pickAudio() {
    const result=await DocumentPicker.getDocumentAsync({type:['audio/mpeg','audio/wav','audio/x-wav','audio/flac','audio/mp4','audio/*'],copyToCacheDirectory:true});
    if(!result.canceled) setAudio(result.assets[0]);
  }
  async function pickCover() {
    const permission=await ImagePicker.requestMediaLibraryPermissionsAsync();
    if(!permission.granted){setError('Photo access is required to choose artwork.');return;}
    const result=await ImagePicker.launchImageLibraryAsync({mediaTypes:['images'],quality:0.85});
    if(!result.canceled) setCover(result.assets[0]);
  }
  async function submit() {
    setError('');setSuccess('');
    if(!title.trim()||!audio){setError('Add a title and choose an audio file.');return;}
    if(!price.trim()||Number(price)<=0){setError('Enter a price greater than zero.');return;}
    setBusy(true);
    try {
      const token=await getAccessToken();
      if(!token) throw new Error('Your session has expired. Please sign in again.');
      const form=new FormData();
      form.append('title',title.trim()); form.append('description',description.trim()); form.append('genre',genre.trim()); form.append('bpm',bpm.trim()); form.append('tags',tags.trim()); form.append('price',price.trim()); form.append('currency',currency); form.append('sales_model',salesModel);
      form.append('audio_file',{uri:audio.uri,name:audio.name||'track.mp3',type:audio.mimeType||'audio/mpeg'} as any);
      if(cover) form.append('cover_file',{uri:cover.uri,name:cover.fileName||'cover.jpg',type:cover.mimeType||'image/jpeg'} as any);
      const response=await fetch('https://mybeathub.com/api/v1/creator/tracks',{method:'POST',headers:{Authorization:`Bearer ${token}`},body:form});
      const text=await response.text(); let data:any={}; try{data=JSON.parse(text)}catch{}
      if(!response.ok) throw new Error(data.detail||data.message||'Upload failed.');
      setSuccess('Track uploaded and published successfully.');
      setTimeout(()=>router.replace('/creator-dashboard'),700);
    } catch(e){setError(e instanceof Error?e.message:'Upload failed.');}
    finally{setBusy(false);}
  }

  return <SafeAreaView style={s.safe}><ScrollView contentContainerStyle={s.container} keyboardShouldPersistTaps="handled">
    <Pressable onPress={()=>router.back()}><Text style={s.back}>‹ Creator Studio</Text></Pressable>
    <Text style={s.title}>Upload Beat / Track</Text><Text style={s.sub}>Add your music, artwork and pricing. The track will be published to your store.</Text>
    <Text style={s.label}>Audio file *</Text><Pressable style={s.pick} onPress={pickAudio}><Text style={s.pickTitle}>{audio?.name||'Choose audio file'}</Text><Text style={s.pickSub}>MP3, WAV, M4A or FLAC</Text></Pressable>
    <Text style={s.label}>Artwork</Text><Pressable style={s.pick} onPress={pickCover}>{cover?<View style={s.coverRow}><Image source={{uri:cover.uri}} style={s.cover}/><Text style={s.pickTitle}>Change artwork</Text></View>:<><Text style={s.pickTitle}>Choose cover artwork</Text><Text style={s.pickSub}>JPG or PNG</Text></>}</Pressable>
    <TextInput style={s.input} placeholder="Track title *" placeholderTextColor="#777180" value={title} onChangeText={setTitle}/>
    <TextInput style={[s.input,s.multiline]} placeholder="Description" placeholderTextColor="#777180" value={description} onChangeText={setDescription} multiline/>
    <View style={s.row}><TextInput style={[s.input,s.half]} placeholder="Genre" placeholderTextColor="#777180" value={genre} onChangeText={setGenre}/><TextInput style={[s.input,s.half]} placeholder="BPM" placeholderTextColor="#777180" value={bpm} onChangeText={setBpm} keyboardType="number-pad"/></View>
    <TextInput style={s.input} placeholder="Tags (comma separated)" placeholderTextColor="#777180" value={tags} onChangeText={setTags}/>
    <Text style={s.label}>Price *</Text><View style={s.row}><TextInput style={[s.input,s.half]} placeholder="Price" placeholderTextColor="#777180" value={price} onChangeText={setPrice} keyboardType="decimal-pad"/><View style={s.half}><Pressable style={[s.choice,currency==='KES'&&s.choiceActive]} onPress={()=>setCurrency('KES')}><Text style={s.choiceText}>KES</Text></Pressable><Pressable style={[s.choice,currency==='USD'&&s.choiceActive]} onPress={()=>setCurrency('USD')}><Text style={s.choiceText}>USD</Text></Pressable></View></View>
    <Text style={s.label}>License</Text><View style={s.row}><Pressable style={[s.choice,s.flex,currency&&salesModel==='non_exclusive'&&s.choiceActive]} onPress={()=>setSalesModel('non_exclusive')}><Text style={s.choiceText}>Non-exclusive</Text></Pressable><Pressable style={[s.choice,s.flex,salesModel==='exclusive'&&s.choiceActive]} onPress={()=>setSalesModel('exclusive')}><Text style={s.choiceText}>Exclusive</Text></Pressable></View>
    {!!error&&<Text style={s.error}>{error}</Text>}{!!success&&<Text style={s.success}>{success}</Text>}
    <Pressable style={s.button} onPress={submit} disabled={busy}>{busy?<ActivityIndicator/>:<Text style={s.buttonText}>Upload & Publish</Text>}</Pressable>
  </ScrollView></SafeAreaView>
}
const s=StyleSheet.create({safe:{flex:1,backgroundColor:'#0d0b12'},container:{padding:22,paddingBottom:45},back:{color:'#aaa3b4',fontSize:15,marginTop:8},title:{color:'#fff',fontSize:30,fontWeight:'800',marginTop:18},sub:{color:'#918b9b',lineHeight:21,marginTop:7,marginBottom:20},label:{color:'#fff',fontSize:13,fontWeight:'700',marginTop:13,marginBottom:7},pick:{backgroundColor:'#181520',borderRadius:14,padding:16,marginBottom:4},pickTitle:{color:'#fff',fontWeight:'700'},pickSub:{color:'#777180',marginTop:5},input:{backgroundColor:'#181520',borderRadius:12,padding:15,color:'#fff',marginTop:10,flex:1},multiline:{minHeight:90,textAlignVertical:'top'},row:{flexDirection:'row',gap:10,alignItems:'center'},half:{flex:1},coverRow:{flexDirection:'row',alignItems:'center',gap:12},cover:{width:55,height:55,borderRadius:9},choice:{borderWidth:1,borderColor:'#302b38',borderRadius:11,padding:13,marginTop:10,alignItems:'center'},choiceActive:{backgroundColor:'#fff',borderColor:'#fff'},choiceText:{color:'#fff',fontWeight:'700'},flex:{flex:1},button:{backgroundColor:'#fff',borderRadius:13,padding:16,alignItems:'center',marginTop:24},buttonText:{color:'#0d0b12',fontWeight:'800'},error:{color:'#ff8f8f',marginTop:14,lineHeight:20},success:{color:'#b8f5c7',marginTop:14,lineHeight:20}});
